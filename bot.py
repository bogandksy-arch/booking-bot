"""
Демо-бот запису клієнтів для Telegram (салон, клініка, майстерня тощо).

Команди:
  /start        — привітання
  /book         — почати запис (вибір послуги → дати → часу)
  /mybookings   — мої активні записи
  /cancel <id>  — скасувати запис за id
  /help         — довідка

Налаштування "під клієнта" — редагуйте лише розділ CONFIG нижче:
  - SERVICES: список послуг
  - WORK_HOURS: робочі години
  - SLOT_MINUTES: крок між слотами
  - BUSINESS_NAME: назва бізнесу для привітання

Запуск:
  1. pip install -r requirements.txt
  2. export TELEGRAM_BOT_TOKEN="токен_від_BotFather"
  3. export ADMIN_CHAT_ID="ваш_telegram_id"   (щоб отримувати сповіщення про нові записи)
  4. python bot.py
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from anthropic import Anthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIG — редагуйте це під конкретного клієнта
# ---------------------------------------------------------------------------

BUSINESS_NAME = "Салон краси «Демо»"

SERVICES = {
    "haircut": "Стрижка",
    "coloring": "Фарбування",
    "manicure": "Манікюр",
    "massage": "Масаж обличчя",
}

WORK_HOURS = list(range(9, 19))  # з 9:00 до 18:00
SLOT_MINUTES = 60                # крок між слотами (хв)
DAYS_AHEAD = 7                   # на скільки днів вперед показувати запис

TZ = ZoneInfo("Europe/Kyiv")
DATA_FILE = Path(__file__).parent / "bookings.json"
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
AI_MODEL = "claude-sonnet-4-6"
ai_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

UA_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
UA_MONTHS = [
    "січ", "лют", "бер", "кві", "тра", "чер",
    "лип", "сер", "вер", "жов", "лис", "гру",
]

# ---------------------------------------------------------------------------
# Зберігання записів
# ---------------------------------------------------------------------------

def load_bookings() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_bookings(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def slot_taken(data: dict, date_str: str, time_str: str) -> bool:
    return any(
        b["date"] == date_str and b["time"] == time_str
        for b in data.values()
    )


def fmt_date(d: datetime) -> str:
    return f"{UA_WEEKDAYS[d.weekday()]} {d.day} {UA_MONTHS[d.month - 1]}"


# ---------------------------------------------------------------------------
# /start, /help
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"Вітаємо у {BUSINESS_NAME}! 👋\n\n"
        "/book — записатися на послугу\n"
        "/mybookings — мої записи\n"
        "/help — довідка"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "/book — обрати послугу, дату й час запису\n"
        "/mybookings — переглянути активні записи\n"
        "/cancel <id> — скасувати запис за id"
    )


# ---------------------------------------------------------------------------
# /book — вибір послуги
# ---------------------------------------------------------------------------

async def book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"svc:{key}")]
        for key, name in SERVICES.items()
    ]
    await update.effective_message.reply_text(
        "Оберіть послугу:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_service_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    service_key = query.data.split(":", 1)[1]

    now = datetime.now(TZ)
    buttons = []
    for i in range(DAYS_AHEAD):
        day = now + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        buttons.append(
            [InlineKeyboardButton(fmt_date(day), callback_data=f"date:{service_key}:{date_str}")]
        )

    await query.edit_message_text(
        f"Послуга: {SERVICES[service_key]}\nОберіть день:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, service_key, date_str = query.data.split(":", 2)

    data = load_bookings()
    now = datetime.now(TZ)
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)

    buttons = []
    row = []
    for hour in WORK_HOURS:
        slot_time = day.replace(hour=hour, minute=0)
        if slot_time <= now:
            continue
        time_str = f"{hour:02d}:00"
        if slot_taken(data, date_str, time_str):
            continue
        row.append(
            InlineKeyboardButton(time_str, callback_data=f"time:{service_key}:{date_str}:{time_str}")
        )
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if not buttons:
        await query.edit_message_text(
            "На цей день вільних слотів немає 😔 Оберіть /book ще раз і виберіть інший день."
        )
        return

    await query.edit_message_text(
        f"Послуга: {SERVICES[service_key]}\nДень: {fmt_date(day)}\nОберіть час:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_time_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, service_key, date_str, time_str = query.data.split(":", 3)

    data = load_bookings()
    if slot_taken(data, date_str, time_str):
        await query.edit_message_text("На жаль, цей час щойно зайняли. Спробуйте /book ще раз.")
        return

    user = query.from_user
    booking_id = uuid.uuid4().hex[:6]
    data[booking_id] = {
        "chat_id": query.message.chat_id,
        "user_id": user.id,
        "user_name": user.full_name,
        "username": user.username or "",
        "service": SERVICES[service_key],
        "date": date_str,
        "time": time_str,
        "created_at": datetime.now(TZ).isoformat(),
    }
    save_bookings(data)

    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    confirmation = (
        f"Готово ✅\n\n"
        f"Послуга: {SERVICES[service_key]}\n"
        f"Дата: {fmt_date(day)}\n"
        f"Час: {time_str}\n"
        f"id запису: {booking_id}\n\n"
        f"Щоб скасувати: /cancel {booking_id}"
    )
    await query.edit_message_text(confirmation)

    if ADMIN_CHAT_ID:
        contact = f"@{user.username}" if user.username else user.full_name
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"🆕 Новий запис!\n"
                    f"Клієнт: {contact}\n"
                    f"Послуга: {SERVICES[service_key]}\n"
                    f"Дата: {fmt_date(day)}, {time_str}\n"
                    f"id: {booking_id}"
                ),
            )
        except Exception:
            logger.exception("Не вдалося надіслати сповіщення адміну")


# ---------------------------------------------------------------------------
# /mybookings, /cancel
# ---------------------------------------------------------------------------

async def my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    data = load_bookings()
    mine = {k: v for k, v in data.items() if v["user_id"] == user_id}

    if not mine:
        await update.effective_message.reply_text("У вас немає активних записів. Скористайтесь /book.")
        return

    items = sorted(mine.items(), key=lambda kv: (kv[1]["date"], kv[1]["time"]))
    lines = ["Ваші записи:"]
    for booking_id, b in items:
        lines.append(f"• [{booking_id}] {b['date']} {b['time']} — {b['service']}")
    lines.append("\nСкасувати: /cancel <id>")

    await update.effective_message.reply_text("\n".join(lines))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Вкажіть id запису: /cancel <id>")
        return

    booking_id = context.args[0]
    data = load_bookings()
    booking = data.get(booking_id)

    if not booking or booking["user_id"] != update.effective_user.id:
        await update.effective_message.reply_text("Запис з таким id не знайдено серед ваших.")
        return

    del data[booking_id]
    save_bookings(data)
    await update.effective_message.reply_text("Запис скасовано.")

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ Скасовано запис id {booking_id} ({booking['service']}, {booking['date']} {booking['time']})",
            )
        except Exception:
            logger.exception("Не вдалося надіслати сповіщення адміну")


# ---------------------------------------------------------------------------
# AI-запис вільним текстом (працює, якщо задано ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

def build_ai_prompt(user_text: str, now: datetime) -> str:
    services_list = "\n".join(f"- {name} (код: {key})" for key, name in SERVICES.items())
    allowed_dates = [
        (now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS_AHEAD)
    ]
    return (
        f"Ти — асистент запису клієнтів у «{BUSINESS_NAME}» у Telegram-боті.\n"
        f"Сьогодні: {now.strftime('%Y-%m-%d')} ({UA_WEEKDAYS[now.weekday()]}), "
        f"час зараз {now.strftime('%H:%M')}.\n\n"
        f"Доступні послуги:\n{services_list}\n\n"
        f"Робочі години: з {WORK_HOURS[0]}:00 до {WORK_HOURS[-1]}:00, "
        f"запис можливий на дати: {', '.join(allowed_dates)}.\n\n"
        f'Повідомлення клієнта: "{user_text}"\n\n'
        "Визнач намір клієнта і поверни ЛИШЕ JSON (без пояснень, без markdown), рівно в такому форматі:\n"
        '{"service": "код_послуги або null", "date": "YYYY-MM-DD або null", '
        '"time": "HH:MM або null", '
        '"reply": "коротка дружня відповідь клієнту українською — якщо чогось не вистачає, постав уточнююче питання"}'
    )


def parse_ai_json(raw_text: str) -> dict | None:
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def ai_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ai_client is None:
        return  # AI-режим вимкнено (нема ANTHROPIC_API_KEY) — ігноруємо вільний текст

    user_text = update.effective_message.text
    now = datetime.now(TZ)

    try:
        response = ai_client.messages.create(
            model=AI_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": build_ai_prompt(user_text, now)}],
        )
        raw_text = response.content[0].text
    except Exception:
        logger.exception("Помилка звернення до Claude API")
        await update.effective_message.reply_text(
            "Вибачте, зараз не можу обробити повідомлення. Спробуйте /book."
        )
        return

    parsed = parse_ai_json(raw_text)
    if not parsed:
        await update.effective_message.reply_text(
            "Не зовсім зрозумів. Спробуйте описати інакше або скористайтесь /book."
        )
        return

    service_key = parsed.get("service")
    date_str = parsed.get("date")
    time_str = parsed.get("time")
    reply_text = parsed.get("reply") or ""

    has_all_fields = (
        service_key in SERVICES
        and date_str
        and time_str
        and re.match(r"^\d{2}:\d{2}$", time_str)
    )

    if not has_all_fields:
        await update.effective_message.reply_text(reply_text or "Уточніть, будь ласка, деталі запису.")
        return

    data = load_bookings()
    if slot_taken(data, date_str, time_str):
        await update.effective_message.reply_text(
            f"На жаль, {date_str} о {time_str} вже зайнято. Спробуйте інший час або /book."
        )
        return

    user = update.effective_user
    booking_id = uuid.uuid4().hex[:6]
    data[booking_id] = {
        "chat_id": update.effective_chat.id,
        "user_id": user.id,
        "user_name": user.full_name,
        "username": user.username or "",
        "service": SERVICES[service_key],
        "date": date_str,
        "time": time_str,
        "created_at": now.isoformat(),
    }
    save_bookings(data)

    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    await update.effective_message.reply_text(
        f"Готово ✅\n\n"
        f"Послуга: {SERVICES[service_key]}\n"
        f"Дата: {fmt_date(day)}\n"
        f"Час: {time_str}\n"
        f"id запису: {booking_id}\n\n"
        f"Щоб скасувати: /cancel {booking_id}"
    )

    if ADMIN_CHAT_ID:
        contact = f"@{user.username}" if user.username else user.full_name
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"🆕 Новий запис (через AI-чат)!\n"
                    f"Клієнт: {contact}\n"
                    f"Послуга: {SERVICES[service_key]}\n"
                    f"Дата: {fmt_date(day)}, {time_str}\n"
                    f"id: {booking_id}"
                ),
            )
        except Exception:
            logger.exception("Не вдалося надіслати сповіщення адміну")


# ---------------------------------------------------------------------------
# Точка входу
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не знайдено TELEGRAM_BOT_TOKEN. Встановіть змінну середовища перед запуском."
        )

    app = Application.builder().token(token).build()

    channel_and_messages = filters.UpdateType.MESSAGES | filters.UpdateType.CHANNEL_POSTS

    app.add_handler(CommandHandler("start", start, filters=channel_and_messages))
    app.add_handler(CommandHandler("help", help_command, filters=channel_and_messages))
    app.add_handler(CommandHandler("book", book, filters=channel_and_messages))
    app.add_handler(CommandHandler("mybookings", my_bookings, filters=channel_and_messages))
    app.add_handler(CommandHandler("cancel", cancel, filters=channel_and_messages))

    app.add_handler(CallbackQueryHandler(on_service_chosen, pattern=r"^svc:"))
    app.add_handler(CallbackQueryHandler(on_date_chosen, pattern=r"^date:"))
    app.add_handler(CallbackQueryHandler(on_time_chosen, pattern=r"^time:"))

    # Вільний текст у приватних чатах (не команди) — обробляє AI, якщо є ключ
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, ai_handle_message)
    )

    logger.info("Бот запису запущено.")
    app.run_polling()


if __name__ == "__main__":
    main()
