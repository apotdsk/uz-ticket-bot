"""
Інтерактивний Telegram-бот для відстеження квитків UZ. Мультикористувацький:
кожен, хто напише боту, керує СВОЇМИ обраними маршрутами незалежно від
інших (маршрути й сповіщення прив'язані до chat_id).

Меню:
  🔍 Обрати новий рейс — покроковий діалог: дати -> звідки -> куди -> пересадки.
                          Доступно, лише якщо у ЦЬОГО користувача ще немає
                          активного маршруту.
  ⚙️ Налаштування та обрані рейси — список обраних маршрутів ЦЬОГО
                          користувача (з номером), кнопка скинути кожен.

Фоновий цикл (JobQueue) раз на POLL_INTERVAL_SEC перевіряє маршрути ВСІХ
користувачів і сповіщає кожного ЛИШЕ коли конкретний рейс (потяг+дата+клас,
або вся пересадкова комбінація) стає доступним ВПЕРШЕ (перехід з "місць
немає" в "місця є") — не на кожне збільшення кількості місць.

Запуск: python bot.py
"""

from __future__ import annotations

import asyncio
import calendar as calendar_module
import logging
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from formatting import format_trip_message
from state import extract_availability, load_state, save_state, trip_key
from storage import add_route, load_all_routes, load_routes, remove_route
from uz_client import IMPERSONATE_PROFILES, UZClient, UZClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
POLL_INTERVAL_SEC = int(os.environ.get("POLL_INTERVAL_SEC", 900))

# Стани діалогу вибору маршруту
CHOOSING_DATE_MODE, PICKING_DATE, CHOOSING_FROM, PICK_FROM, CHOOSING_TO, PICK_TO, CHOOSING_TRANSFERS = range(7)

UKR_MONTHS = [
    "", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень",
]
UKR_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def build_calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    """Інлайн-календар на конкретний місяць. Минулі дні — непатабельні заглушки."""
    today = date.today()
    weeks = calendar_module.Calendar(firstweekday=0).monthdayscalendar(year, month)

    rows = [
        [InlineKeyboardButton(f"{UKR_MONTHS[month]} {year}", callback_data="cal:noop")],
        [InlineKeyboardButton(wd, callback_data="cal:noop") for wd in UKR_WEEKDAYS],
    ]

    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal:noop"))
                continue
            d = date(year, month, day)
            if d < today:
                row.append(InlineKeyboardButton(" ", callback_data="cal:noop"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"cal:pick:{d.isoformat()}"))
        rows.append(row)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    rows.append(
        [
            InlineKeyboardButton("◀", callback_data=f"cal:nav:{prev_year}-{prev_month:02d}"),
            InlineKeyboardButton("▶", callback_data=f"cal:nav:{next_year}-{next_month:02d}"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔍 Обрати новий рейс", callback_data="menu:new_route")],
            [InlineKeyboardButton("⚙️ Налаштування та обрані рейси", callback_data="menu:settings")],
        ]
    )


# ---------- Головне меню ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_chat.send_message("Вітаю! Що робимо?", reply_markup=main_menu_keyboard())


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Що робимо?", reply_markup=main_menu_keyboard())


# ---------- Налаштування / обрані рейси ----------

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    routes = load_routes(chat_id)

    if not routes:
        await query.edit_message_text("Обраних маршрутів немає.", reply_markup=main_menu_keyboard())
        return

    lines = []
    buttons = []
    # Показуємо порядковий номер СЕРЕД ПОТОЧНИХ активних маршрутів (1, 2, ...),
    # а не внутрішній r["id"] (той — наскрізний лічильник, що ніколи не
    # повторюється навіть після видалення, потрібен лише для дедублікації
    # сповіщень у state.py — користувачу його бачити нема сенсу).
    for display_num, r in enumerate(routes, start=1):
        suffix = " (з пересадками)" if r.get("with_transfers") else ""
        lines.append(
            f"#{display_num}: {r['station_from_name']} → {r['station_to_name']}{suffix}\n"
            f"   дати: {', '.join(r['dates'])}"
        )
        buttons.append(
            [InlineKeyboardButton(f"🗑 Скинути #{display_num}", callback_data=f"reset:{r['id']}")]
        )
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:back")])

    await query.edit_message_text("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def reset_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    route_id = int(query.data.split(":")[1])

    # Позиційний номер треба взяти ДО видалення — після нього маршрут
    # зникне зі списку. Рахуємо лише серед маршрутів ЦЬОГО chat_id.
    routes_before = load_routes(chat_id)
    display_num = next(
        (i + 1 for i, r in enumerate(routes_before) if r["id"] == route_id), None
    )

    removed = remove_route(chat_id, route_id)
    if removed:
        text = f"Маршрут #{display_num} скинуто."
    else:
        text = "Маршрут не знайдено (можливо, вже скинутий)."
    await query.edit_message_text(text, reply_markup=main_menu_keyboard())


# ---------- Діалог вибору нового маршруту ----------

async def new_route_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    existing = load_routes(update.effective_chat.id)
    if existing:
        r = existing[0]
        await query.edit_message_text(
            f"У вас вже є обраний маршрут #1: {r['station_from_name']} → "
            f"{r['station_to_name']}.\nСпершу скиньте його в налаштуваннях, щоб обрати новий.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    context.user_data.clear()
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 Одна дата", callback_data="datemode:single")],
            [InlineKeyboardButton("📅 Проміжок дат", callback_data="datemode:range")],
        ]
    )
    await query.edit_message_text("Крок 1/4. Оберіть режим дати:", reply_markup=keyboard)
    return CHOOSING_DATE_MODE


async def date_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":")[1]  # "single" | "range"
    context.user_data["date_mode"] = mode
    context.user_data.pop("range_start", None)

    today = date.today()
    prompt = "Оберіть дату:" if mode == "single" else "Оберіть дату початку проміжку:"
    await query.edit_message_text(prompt, reply_markup=build_calendar_keyboard(today.year, today.month))
    return PICKING_DATE


async def calendar_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    year, month = map(int, query.data.split(":")[2].split("-"))

    mode = context.user_data.get("date_mode", "single")
    waiting_for_end = mode == "range" and "range_start" in context.user_data
    prompt = "Оберіть дату кінця проміжку:" if waiting_for_end else (
        "Оберіть дату початку проміжку:" if mode == "range" else "Оберіть дату:"
    )
    await query.edit_message_text(prompt, reply_markup=build_calendar_keyboard(year, month))
    return PICKING_DATE


async def calendar_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return PICKING_DATE


async def calendar_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    picked = query.data.split(":")[2]  # YYYY-MM-DD
    mode = context.user_data.get("date_mode", "single")

    if mode == "single":
        context.user_data["dates"] = [picked]
        await query.edit_message_text(
            f"Дата: {picked}.\n\nКрок 2/4. Напишіть станцію відправлення (назву):",
            reply_markup=None,
        )
        return CHOOSING_FROM

    if "range_start" not in context.user_data:
        context.user_data["range_start"] = picked
        year, month, _ = (int(p) for p in picked.split("-"))
        await query.edit_message_text(
            f"Початок проміжку: {picked}.\nОберіть дату кінця проміжку:",
            reply_markup=build_calendar_keyboard(year, month),
        )
        return PICKING_DATE

    start = context.user_data.pop("range_start")
    end = picked
    if end < start:
        start, end = end, start
    start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
    dates = [(start_d + timedelta(days=i)).isoformat() for i in range((end_d - start_d).days + 1)]
    context.user_data["dates"] = dates

    await query.edit_message_text(
        f"Проміжок: {start} — {end} ({len(dates)} дн.).\n\n"
        "Крок 2/4. Напишіть станцію відправлення (назву):",
        reply_markup=None,
    )
    return CHOOSING_FROM


async def _offer_station_candidates(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str, direction: str
) -> int:
    client: UZClient = context.bot_data["uz_client"]
    try:
        candidates = await asyncio.to_thread(client.suggest_stations, query_text)
    except UZClientError as exc:
        await update.effective_chat.send_message(f"Помилка пошуку станції: {exc}\nСпробуйте ще раз:")
        return CHOOSING_FROM if direction == "from" else CHOOSING_TO

    if not candidates:
        await update.effective_chat.send_message("Нічого не знайшов за цією назвою. Спробуйте ще раз:")
        return CHOOSING_FROM if direction == "from" else CHOOSING_TO

    # API не гарантує, що точний збіг буде першим у списку — сортуємо самі,
    # інакше потрібна станція може випасти за межі видимого ліміту кнопок.
    normalized_query = query_text.strip().lower()

    def _priority(candidate: dict) -> tuple[int, str]:
        name = candidate["name"].strip().lower()
        if name == normalized_query:
            rank = 0
        elif name.startswith(normalized_query):
            rank = 1
        elif normalized_query in name:
            rank = 2
        else:
            rank = 3
        return (rank, candidate["name"])

    candidates = sorted(candidates, key=_priority)

    if len(candidates) == 1 or candidates[0]["name"].strip().lower() == normalized_query:
        return await _station_chosen(update, context, candidates[0], direction)

    LIMIT = 20
    context.user_data[f"{direction}_candidates"] = candidates[:LIMIT]
    buttons = [
        [InlineKeyboardButton(c["name"], callback_data=f"pick_{direction}:{i}")]
        for i, c in enumerate(candidates[:LIMIT])
    ]
    note = "" if len(candidates) <= LIMIT else f" (показано перші {LIMIT} з {len(candidates)})"
    await update.effective_chat.send_message(
        f"Знайшов кілька варіантів, оберіть потрібний{note}:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return PICK_FROM if direction == "from" else PICK_TO


async def _station_chosen(
    update: Update, context: ContextTypes.DEFAULT_TYPE, candidate: dict, direction: str
) -> int:
    context.user_data[f"station_{direction}_id"] = candidate["id"]
    context.user_data[f"station_{direction}_name"] = candidate["name"]

    if direction == "from":
        await update.effective_chat.send_message(
            f"Звідки: {candidate['name']}.\n\nКрок 3/4. Напишіть станцію призначення (назву):"
        )
        return CHOOSING_TO

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Так", callback_data="transfers:yes"),
          InlineKeyboardButton("Ні", callback_data="transfers:no")]]
    )
    await update.effective_chat.send_message(
        f"Куди: {candidate['name']}.\n\nКрок 4/4. Враховувати рейси з пересадкою?",
        reply_markup=keyboard,
    )
    return CHOOSING_TRANSFERS


async def from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _offer_station_candidates(update, context, update.message.text.strip(), "from")


async def to_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _offer_station_candidates(update, context, update.message.text.strip(), "to")


async def pick_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    candidate = context.user_data["from_candidates"][idx]
    await query.edit_message_text(f"Обрано: {candidate['name']}", reply_markup=None)
    return await _station_chosen(update, context, candidate, "from")


async def pick_to_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    candidate = context.user_data["to_candidates"][idx]
    await query.edit_message_text(f"Обрано: {candidate['name']}", reply_markup=None)
    return await _station_chosen(update, context, candidate, "to")


async def transfers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    with_transfers = query.data.split(":")[1] == "yes"

    ud = context.user_data
    route = {
        "station_from_name": ud["station_from_name"],
        "station_from_id": ud["station_from_id"],
        "station_to_name": ud["station_to_name"],
        "station_to_id": ud["station_to_id"],
        "dates": ud["dates"],
        "with_transfers": with_transfers,
    }
    route_id = add_route(chat_id, route)
    context.user_data.clear()

    # Одразу запускаємо позачергову перевірку САМЕ ЦЬОГО маршруту (не всіх
    # користувачів), а не чекаємо до наступного планового циклу.
    route_full = {**route, "id": route_id, "chat_id": chat_id}
    context.job_queue.run_once(
        immediate_check_job, when=1, data={"chat_id": chat_id, "route": route_full}
    )

    await query.edit_message_text(
        f"✅ Маршрут #1 збережено: {route['station_from_name']} → "
        f"{route['station_to_name']}, дати: {', '.join(route['dates'])}"
        f"{' (з пересадками)' if with_transfers else ''}.\n\n"
        f"Перевіряю зараз, і далі — кожні {POLL_INTERVAL_SEC // 60} хв.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.effective_chat.send_message("Скасовано.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ---------- Фонова перевірка ----------

async def _check_route(
    context: ContextTypes.DEFAULT_TYPE,
    client: UZClient,
    lock: asyncio.Lock,
    route: dict,
    state: dict,
    display_num: int,
) -> tuple[bool, int, list[str]]:
    """
    Перевіряє ОДИН маршрут по всіх його датах, мутує переданий state на
    місці. Повертає (чи щось надіслано, скільки трипів побачено всього,
    список помилок). Спільна для фонового циклу й миттєвої перевірки.

    lock серіалізує звернення до спільного UZClient — curl_cffi Session
    не гарантовано безпечна для одночасних запитів з різних потоків,
    а клієнт один на всіх користувачів (asyncio.to_thread виконує
    блокуючі виклики в пулі потоків).
    """
    sent_any = False
    total_trips = 0
    errors: list[str] = []

    for trip_date in route["dates"]:
        try:
            async with lock:
                trips = await asyncio.to_thread(
                    client.search_trips,
                    route["station_from_id"],
                    route["station_to_id"],
                    trip_date,
                    route.get("with_transfers", False),
                )
        except UZClientError as exc:
            logger.warning(
                "Помилка для маршруту #%s (id=%s) %s: %s",
                display_num, route["id"], trip_date, exc,
            )
            errors.append(f"#{display_num} {trip_date}: {exc}")
            continue

        logger.info(
            "Маршрут #%s (id=%s) %s: знайдено %d трипів (прямих+пересадкових)",
            display_num, route["id"], trip_date, len(trips),
        )
        total_trips += len(trips)

        for trip in trips:
            availability = extract_availability(trip)
            key = trip_key(f"route{route['id']}", trip_date, trip)
            prev = state.get(key, {})

            # Сповіщаємо лише про ПЕРШУ появу (перехід "місць немає" -> "є"),
            # не на кожне подальше збільшення кількості.
            became_available = (not prev) and bool(availability)
            if became_available:
                label = f"#{display_num} {route['station_from_name']} → {route['station_to_name']}"
                text = format_trip_message(label, trip_date, trip)
                await context.bot.send_message(
                    chat_id=route["chat_id"], text=text, parse_mode=ParseMode.HTML,
                    reply_markup=main_menu_keyboard(),
                )
                # chat_id свідомо НЕ логуємо — логи GitHub Actions публічні
                # для публічних репо, а chat_id + маршрут/дата разом це вже
                # персональні дані.
                logger.info("Сповіщення надіслано: %s", key)
                sent_any = True

            state[key] = availability

    return sent_any, total_trips, errors


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Регулярний фоновий цикл — перевіряє маршрути АБСОЛЮТНО ВСІХ
    користувачів. Мовчить, якщо нічого нового не знайшлось (щоб не
    спамити) — на відміну від immediate_check_job.
    """
    client: UZClient = context.bot_data["uz_client"]
    lock: asyncio.Lock = context.bot_data["uz_client_lock"]
    routes = load_all_routes()
    if not routes:
        return

    state = load_state()

    # Нумерація #1, #2... рахується ОКРЕМО для кожного користувача — в
    # межах ЙОГО списку маршрутів, а не спільна на всіх.
    per_chat_counter: dict[int, int] = {}
    for route in routes:
        chat_id = route["chat_id"]
        per_chat_counter[chat_id] = per_chat_counter.get(chat_id, 0) + 1
        display_num = per_chat_counter[chat_id]
        await _check_route(context, client, lock, route, state, display_num)

    save_state(state)


async def immediate_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Позачергова перевірка ОДНОГО щойно доданого маршруту (не всіх
    користувачів) — завжди звітує в чат власника, навіть "місць немає",
    щоб не гадати, чи щось зламалось.
    """
    chat_id = context.job.data["chat_id"]
    route = context.job.data["route"]

    client: UZClient = context.bot_data["uz_client"]
    lock: asyncio.Lock = context.bot_data["uz_client_lock"]
    state = load_state()

    sent_any, total_trips, errors = await _check_route(context, client, lock, route, state, display_num=1)
    save_state(state)

    if not sent_any:
        if errors:
            summary = "⚠️ Перевірку виконано з помилками:\n" + "\n".join(errors)
        else:
            summary = (
                f"🔎 Перевірку виконано, розглянуто {total_trips} рейсів — "
                "вільних місць поки немає."
            )
        await context.bot.send_message(chat_id=chat_id, text=summary, reply_markup=main_menu_keyboard())


def ensure_connectivity(client: UZClient) -> None:
    while True:
        try:
            client.ping()
            return
        except UZClientError as exc:
            logger.warning("Профіль %s заблокований: %s", client.impersonate, exc)
            if not client.try_other_profile():
                raise UZClientError(
                    f"Жоден з профілів impersonate не пройшов ping ({IMPERSONATE_PROFILES})."
                ) from exc


def main() -> None:
    client = UZClient()
    ensure_connectivity(client)

    application = Application.builder().token(BOT_TOKEN).build()
    application.bot_data["uz_client"] = client
    application.bot_data["uz_client_lock"] = asyncio.Lock()

    new_route_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_route_entry, pattern="^menu:new_route$")],
        states={
            CHOOSING_DATE_MODE: [CallbackQueryHandler(date_mode_callback, pattern="^datemode:")],
            PICKING_DATE: [
                CallbackQueryHandler(calendar_nav_callback, pattern="^cal:nav:"),
                CallbackQueryHandler(calendar_pick_callback, pattern="^cal:pick:"),
                CallbackQueryHandler(calendar_noop, pattern="^cal:noop$"),
            ],
            CHOOSING_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, from_text)],
            PICK_FROM: [CallbackQueryHandler(pick_from_callback, pattern="^pick_from:")],
            CHOOSING_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, to_text)],
            PICK_TO: [CallbackQueryHandler(pick_to_callback, pattern="^pick_to:")],
            CHOOSING_TRANSFERS: [CallbackQueryHandler(transfers_callback, pattern="^transfers:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(new_route_conv)
    application.add_handler(CallbackQueryHandler(show_settings, pattern="^menu:settings$"))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern="^menu:back$"))
    application.add_handler(CallbackQueryHandler(reset_route, pattern="^reset:"))

    application.job_queue.run_repeating(poll_job, interval=POLL_INTERVAL_SEC, first=10)

    logger.info("Бот запущений. Інтервал перевірки: %d сек.", POLL_INTERVAL_SEC)
    application.run_polling()


if __name__ == "__main__":
    main()
