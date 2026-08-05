"""
Витягуємо з сирого JSON-трипу компактний "відбиток" наявності місць і
порівнюємо з тим, що вже бачили — щоб не спамити однаковими сповіщеннями
кожні 15-30 хв, а сповіщати лише про НОВУ появу місць.

Структура трипу підтверджена реальною відповіддю app.uz.gov.ua/api/v3/trips:
    {
      "id": 12378724,
      "depart_at": 1786293300,   # unix timestamp
      "arrive_at": 1786310520,
      "train": {
        "number": "008Л",
        "wagon_classes": [
          {"id": "П", "name": "Плацкарт", "free_seats": 0, "price": 0, ...},
          {"id": "К", "name": "Купе", "free_seats": 0, "price": 0, ...},
          ...
        ]
      },
      ...
    }

Стан зберігається у простому JSON-файлі (для одного користувача цього
цілком достатньо; для десятків маршрутів можна перейти на SQLite).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import crypto_store

logger = logging.getLogger("state")

STATE_FILE = Path(__file__).parent / "state.json"


def load_state() -> dict[str, Any]:
    raw_text = crypto_store.read_text(STATE_FILE, default="")
    if not raw_text:
        return {}
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning("state.json пошкоджений, починаю з чистого стану")
        return {}


def save_state(state: dict[str, Any]) -> None:
    crypto_store.write_text(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))


def leg_availability(leg: dict[str, Any]) -> dict[str, int]:
    """Публічний хелпер: {назва_класу: вільних_місць} (лише >0) для однієї ноги/трипу."""
    result: dict[str, int] = {}
    wagon_classes = leg.get("train", {}).get("wagon_classes", [])
    for wc in wagon_classes:
        name = wc.get("name") or wc.get("id") or "?"
        free = wc.get("free_seats", 0) or 0
        if isinstance(free, (int, float)) and free > 0:
            result[name] = int(free)
    return result


def extract_availability(trip: dict[str, Any]) -> dict[str, int]:
    """
    Для прямого трипу — {назва_класу: вільних_місць} (лише >0).

    Для трипу з пересадкою (trip["legs"] — список ніг маршруту) — подорож
    можлива, лише якщо МІСЦЯ Є НА КОЖНІЙ нозі. Якщо хоч одна нога без місць
    у жодному класі — вважаємо весь варіант недоступним (порожній результат,
    сповіщення не шлеться). Якщо доступний — повертає по кожній нозі окремо,
    з префіксом "Пересадка N:", щоб було видно, за який саме відрізок мова.
    Використовується лише для порівняння стану (дедублікації), не для
    форматування повідомлення — для того є main.py::leg_availability напряму.
    """
    legs = trip.get("legs")
    if legs is None:
        return leg_availability(trip)

    per_leg = [leg_availability(leg) for leg in legs]
    if any(not leg_avail for leg_avail in per_leg):
        return {}  # хоч одна нога без місць -> вся подорож неможлива

    result: dict[str, int] = {}
    for i, leg_avail in enumerate(per_leg, start=1):
        for cls, seats in leg_avail.items():
            result[f"Пересадка {i}: {cls}"] = seats
    return result


def trip_key(route_name: str, trip_date: str, trip: dict[str, Any]) -> str:
    # trip["id"] — унікальний ідентифікатор (для пересадкового трипу —
    # композитний, склеєний з id усіх ніг у search_trips).
    trip_id = trip.get("id")
    if trip_id is not None:
        return f"{route_name}|{trip_date}|{trip_id}"
    train_num = trip.get("train", {}).get("number", "?")
    return f"{route_name}|{trip_date}|{train_num}|{trip.get('depart_at', '')}"
