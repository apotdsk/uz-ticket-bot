"""
Клієнт для внутрішнього API booking.uz.gov.ua (домен app.uz.gov.ua) —
знайдений через DevTools -> Network під час реального пошуку на сайті
(booking.uz.gov.ua сам по собі — SPA-обгортка, дані тягне звідси).

Підтверджений робочий запит (curl, зловлений з браузера):

    GET https://app.uz.gov.ua/api/v3/trips
        ?station_from_id=...&station_to_id=...&with_transfers=0&date=YYYY-MM-DD
    Headers:
        accept: application/json
        origin: https://booking.uz.gov.ua
        referer: https://booking.uz.gov.ua/
        x-client-locale: uk
        x-session-id: <довільний UUID, гостьова сесія>
        x-user-agent: UZ/2 Web/1 User/guest

x-session-id генерується самим клієнтом (браузер робить це на льоту для
анонімного відвідувача) — тут генеруємо один UUID на процес.

Раніше сайт booking.uz.gov.ua блокував "голий" requests за TLS-відбитком —
тому й тут лишаємо curl_cffi (impersonate=chrome) замість звичайного
requests, про всяк випадок, навіть якщо цей ендпоінт виявиться менш
прискіпливим.

НЕ ПЕРЕВІРЕНО МНОЮ НАЖИВО (не маю мережевого доступу до app.uz.gov.ua з
цього середовища) — структура відповіді (список поїздів, поля місць
тощо) невідома, поки ви не запустите й не побачите реальний JSON.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from curl_cffi import requests

logger = logging.getLogger("uz_client")

APP_BASE_URL = "https://app.uz.gov.ua"
BOOKING_ORIGIN = "https://booking.uz.gov.ua"
DEBUG_DIR = Path(__file__).parent

# Профілі TLS/HTTP2-відбитку, які вміє імітувати curl_cffi.
# Якщо один блокується — спробуйте інший.
IMPERSONATE_PROFILES = ["chrome124", "chrome120", "safari17_0"]

USER_AGENTS = {
    "chrome124": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "chrome120": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "safari17_0": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
}


class UZClientError(Exception):
    pass


def _dump_debug(content: str, extension: str) -> None:
    path = DEBUG_DIR / f"debug_last_response.{extension}"
    path.write_text(content, encoding="utf-8")
    logger.info("Сира відповідь збережена у %s (%d символів)", path, len(content))


class UZClient:
    def __init__(self, timeout: int = 15, impersonate: str = "chrome124"):
        self.timeout = timeout
        self.impersonate = impersonate
        self.session = requests.Session(impersonate=impersonate)
        # Одна гостьова сесія на весь процес — так само, як робив би браузер
        # для анонімного відвідувача протягом одного "візиту" на сайт.
        self.session_id = str(uuid.uuid4())

    def try_other_profile(self) -> bool:
        """Перемикається на наступний impersonate-профіль, якщо поточний блокується."""
        try:
            idx = IMPERSONATE_PROFILES.index(self.impersonate)
        except ValueError:
            idx = -1
        if idx + 1 >= len(IMPERSONATE_PROFILES):
            return False
        self.impersonate = IMPERSONATE_PROFILES[idx + 1]
        self.session = requests.Session(impersonate=self.impersonate)
        logger.info("Перемикаюсь на профіль impersonate=%s", self.impersonate)
        return True

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENTS.get(self.impersonate, USER_AGENTS["chrome124"]),
            "Accept": "application/json",
            "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": BOOKING_ORIGIN,
            "Referer": f"{BOOKING_ORIGIN}/",
            "x-client-locale": "uk",
            "x-session-id": self.session_id,
            "x-user-agent": "UZ/2 Web/1 User/guest",
        }

    def ping(self) -> None:
        """
        Перевірка з'єднання: б'є в trips-ендпоінт з мінімальними параметрами
        і кидає UZClientError, якщо запит блокується (403) — щоб main.py міг
        спробувати інший impersonate-профіль ДО того, як почати реальний цикл.
        """
        resp = self.session.get(
            f"{APP_BASE_URL}/api/v3/trips",
            params={
                "station_from_id": "2218500",
                "station_to_id": "2218000",
                "with_transfers": 0,
                "date": "2026-08-09",
            },
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code == 403:
            raise UZClientError(f"403 при ping ({APP_BASE_URL})")

    def suggest_stations(self, query: str) -> list[dict[str, Any]]:
        """
        Пошук станцій за (частиною) назви. Підтверджений робочий запит:

            GET https://app.uz.gov.ua/api/stations?query=...

        Відповідь — простий список [{"id": 2200001, "name": "Київ-Пасажирський"}, ...].
        """
        resp = self.session.get(
            f"{APP_BASE_URL}/api/stations",
            params={"query": query},
            headers=self._headers(),
            timeout=self.timeout,
        )

        if resp.status_code == 403:
            raise UZClientError("403 при пошуку станції.")
        resp.raise_for_status()

        try:
            items = resp.json()
        except ValueError as exc:
            _dump_debug(resp.text, "html")
            raise UZClientError(
                "Відповідь на пошук станції не є JSON. Сира відповідь у "
                "debug_last_response.html."
            ) from exc

        return [
            {"id": str(item["id"]), "name": item["name"]}
            for item in items
            if "id" in item and "name" in item
        ]

    def search_trips(
        self,
        station_from: str,
        station_to: str,
        start_date: str,
        with_transfers: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Повертає список трипів для маршруту на дату start_date (YYYY-MM-DD).

        Прямі трипи (з "direct") повертаються як є — кожен зі своїм
        "train"/"wagon_classes".

        Трипи з пересадкою (з "with_transfer") API повертає як СПИСОК НІГ
        маршруту (кожна нога — окремий потяг зі своїми вагонами/місцями).
        Тут вони обгортаються в синтетичний об'єкт {"legs": [...], "id": ...},
        щоб решта коду (state.py, main.py) могла однаково ітеруватись по
        трипах, перевіряючи trip.get("legs") щоб відрізнити пересадку від
        прямого рейсу.
        """
        resp = self.session.get(
            f"{APP_BASE_URL}/api/v3/trips",
            params={
                "station_from_id": station_from,
                "station_to_id": station_to,
                "with_transfers": 1 if with_transfers else 0,
                "date": start_date,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )

        if resp.status_code == 403:
            raise UZClientError(
                f"403 від {APP_BASE_URL} — бот-детект заблокував запит. "
                "Спробуйте інший impersonate-профіль (client.try_other_profile())."
            )
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError as exc:
            _dump_debug(resp.text, "html")
            raise UZClientError(
                "Відповідь не є JSON — сира відповідь збережена у "
                "debug_last_response.html, погляньте що там."
            ) from exc

        return self._as_trip_list(data)

    @staticmethod
    def _as_trip_list(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []

        trips: list[dict[str, Any]] = list(data.get("direct") or [])

        for legs in data.get("with_transfer") or []:
            if not legs:
                continue
            trips.append(
                {
                    "legs": legs,
                    "id": "+".join(str(leg.get("id", "?")) for leg in legs),
                    "depart_at": legs[0].get("depart_at"),
                }
            )

        if not trips and "direct" not in data:
            _dump_debug(json.dumps(data, ensure_ascii=False, indent=2), "json")
            logger.warning(
                "Відповідь — JSON-об'єкт без очікуваного ключа 'direct'. "
                "Реальні ключі верхнього рівня: %s. Дамп у debug_last_response.json.",
                list(data.keys()),
            )
        return trips
