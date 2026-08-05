"""
Пробує кілька правдоподібних варіантів ендпоінту пошуку станції за назвою
на app.uz.gov.ua (за аналогією зі стилем /api/v3/trips, який підтвердився).
Друкує статус і перші символи відповіді для кожного варіанту — так буде
видно, який (якщо взагалі) повертає осмислений JSON зі списком станцій.

Якщо жоден не спрацює — треба зловити реальний запит через DevTools так
само, як ми зловили /api/v3/trips: почніть друкувати назву станції в полі
пошуку на booking.uz.gov.ua, Network -> XHR/Fetch, знайдіть запит, Copy as
cURL (приберіть значення cookie/токенів) і скажіть мені.

Запуск: python test_stations_search.py
"""

import uuid

from curl_cffi import requests

BASE = "https://app.uz.gov.ua"
HEADERS = {
    "accept": "application/json",
    "accept-language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://booking.uz.gov.ua",
    "referer": "https://booking.uz.gov.ua/",
    "x-client-locale": "uk",
    "x-session-id": str(uuid.uuid4()),
    "x-user-agent": "UZ/2 Web/1 User/guest",
}

QUERY = "Чернівці"

CANDIDATES = [
    ("/api/v3/stations", "query"),
    ("/api/v3/stations", "name"),
    ("/api/v3/stations", "q"),
    ("/api/v3/stations/search", "query"),
    ("/api/v3/station-search", "query"),
    ("/api/v3/stations-search", "query"),
    ("/api/stations", "query"),
]

for path, param_name in CANDIDATES:
    try:
        resp = requests.get(
            f"{BASE}{path}",
            params={param_name: QUERY},
            headers=HEADERS,
            impersonate="chrome124",
            timeout=10,
        )
    except Exception as exc:
        print(f"{path}?{param_name}=... -> помилка запиту: {exc}")
        continue

    ctype = resp.headers.get("content-type", "")
    is_json = "json" in ctype
    marker = "✅" if is_json and resp.status_code == 200 else "  "
    print(f"{marker} {path}?{param_name}=... -> {resp.status_code} ({ctype})")
    if is_json and resp.status_code == 200:
        print("   ", resp.text[:300])
