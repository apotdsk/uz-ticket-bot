"""Форматування сповіщень про наявність місць у Telegram-повідомлення."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from state import leg_availability

KYIV_TZ = ZoneInfo("Europe/Kyiv")


def format_time(unix_ts: int | None) -> str:
    if unix_ts is None:
        return "?"
    return datetime.fromtimestamp(unix_ts, tz=KYIV_TZ).strftime("%H:%M")


def format_trip_message(route_label: str, trip_date: str, trip: dict) -> str:
    """
    Формат:
      🚆 Чернівці - Івано-Франківськ - Львів (з пересадкою)
      Відправлення: 08:04, прибуття: 15:09
      #1 Чернівці → Львів
      2026-08-09

      Потяг №262Л: Чернівці → Івано-Франківськ
      Відправлення: 08:04, прибуття: 10:19
        Купе: 3 місця

      Потяг №043Л: Івано-Франківськ → Львів
      Відправлення: 11:39, прибуття: 15:09
        Жіноче купе: 2 місця
    """
    is_transfer = "legs" in trip
    legs = trip["legs"] if is_transfer else [trip]

    chain = [legs[0].get("station_from", "?")] + [leg.get("station_to", "?") for leg in legs]
    suffix = " (з пересадкою)" if is_transfer else ""
    overall_dep = format_time(legs[0].get("depart_at"))
    overall_arr = format_time(legs[-1].get("arrive_at"))
    header = (
        f"🚆 <b>{' - '.join(chain)}</b>{suffix}\n"
        f"Відправлення: {overall_dep}, прибуття: {overall_arr}\n"
        f"{route_label}\n{trip_date}"
    )

    blocks = []
    for leg in legs:
        train_num = leg.get("train", {}).get("number", "?")
        st_from = leg.get("station_from", "?")
        st_to = leg.get("station_to", "?")
        dep = format_time(leg.get("depart_at"))
        arr = format_time(leg.get("arrive_at"))
        classes = leg_availability(leg)
        class_lines = "\n".join(f"  {cls}: {seats} місць" for cls, seats in classes.items())
        blocks.append(
            f"Потяг №{train_num}: {st_from} → {st_to}\n"
            f"Відправлення: {dep}, прибуття: {arr}\n"
            f"{class_lines}"
        )

    return header + "\n\n" + "\n\n".join(blocks)
