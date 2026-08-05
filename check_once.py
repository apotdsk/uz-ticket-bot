"""
Автономний скрипт для GitHub Actions (або будь-якого cron поза Telegram-ботом):
ОДИН прохід перевірки всіх обраних маршрутів УСІХ користувачів (мультикористувацький
режим — читає той самий selected_routes.json, що й bot.py) + надсилання сповіщень.
Без інтерактивного меню — bot.py для цього має бути живим окремо.

Файли, які має бачити цей скрипт у робочій директорії:
  - selected_routes.json — обрані маршрути (хто саме додав їх — через живий
    bot.py локально/на сервері; сюди лише читаємо).
  - state.json — стан дедублікації сповіщень. Записується назад після
    кожного запуску, щоб наступний прогін знав, про що вже сповіщали.

Потребує TELEGRAM_BOT_TOKEN у середовищі.

Запуск: python check_once.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Bot
from telegram.constants import ParseMode

from formatting import format_trip_message
from state import extract_availability, load_state, save_state, trip_key
from storage import load_all_routes
from uz_client import IMPERSONATE_PROFILES, UZClient, UZClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("check_once")

load_dotenv()
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


def ensure_connectivity(client: UZClient) -> None:
    while True:
        try:
            client.ping()
            return
        except UZClientError as exc:
            logger.warning("Профіль %s заблокований: %s", client.impersonate, exc)
            if not client.try_other_profile():
                raise UZClientError(
                    f"Жоден з профілів impersonate не пройшов ping ({IMPERSONATE_PROFILES}). "
                    "Можливо, IP цього раннера (GitHub Actions — датацентрові IP) заблоковано "
                    "жорсткіше, ніж домашній."
                ) from exc


async def main() -> None:
    client = UZClient()
    ensure_connectivity(client)

    routes = load_all_routes()
    if not routes:
        logger.info("Обраних маршрутів немає (перевірте selected_routes.json) — нічого перевіряти.")
        return

    state = load_state()
    sent_total = 0
    checked_total = 0

    async with Bot(token=BOT_TOKEN) as bot:
        for route in routes:
            for trip_date in route["dates"]:
                try:
                    trips = client.search_trips(
                        route["station_from_id"],
                        route["station_to_id"],
                        trip_date,
                        route.get("with_transfers", False),
                    )
                except UZClientError as exc:
                    logger.warning("Помилка для маршруту id=%s %s: %s", route["id"], trip_date, exc)
                    continue

                logger.info("Маршрут id=%s %s: знайдено %d трипів", route["id"], trip_date, len(trips))
                checked_total += len(trips)

                for trip in trips:
                    availability = extract_availability(trip)
                    key = trip_key(f"route{route['id']}", trip_date, trip)
                    prev = state.get(key, {})

                    became_available = (not prev) and bool(availability)
                    if became_available:
                        label = f"{route['station_from_name']} → {route['station_to_name']}"
                        text = format_trip_message(label, trip_date, trip)
                        await bot.send_message(chat_id=route["chat_id"], text=text, parse_mode=ParseMode.HTML)
                        # chat_id свідомо НЕ логуємо — логи GitHub Actions публічні
                        # для публічних репо.
                        logger.info("Сповіщення надіслано: %s", key)
                        sent_total += 1

                    state[key] = availability

    save_state(state)
    logger.info("Перевірку завершено: розглянуто %d трипів, сповіщень надіслано %d.", checked_total, sent_total)


if __name__ == "__main__":
    asyncio.run(main())
