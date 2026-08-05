"""
Швидкий тест підтвердженого ендпоінту app.uz.gov.ua/api/v3/trips —
зловленого через DevTools -> Network з реального пошуку на сайті.
Друкує сиру відповідь, щоб одразу побачити структуру (чи це JSON, чи
знову блок/challenge).

Запуск: python test_app_api.py
"""

import json
import uuid

from curl_cffi import requests

URL = "https://app.uz.gov.ua/api/v3/trips"
PARAMS = {
    "station_from_id": "2218500",
    "station_to_id": "2218000",
    "with_transfers": 0,
    "date": "2026-08-09",
}
HEADERS = {
    "accept": "application/json",
    "accept-language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://booking.uz.gov.ua",
    "referer": "https://booking.uz.gov.ua/",
    "x-client-locale": "uk",
    "x-session-id": str(uuid.uuid4()),  # гостьова сесія, як генерує браузер
    "x-user-agent": "UZ/2 Web/1 User/guest",
}

resp = requests.get(URL, params=PARAMS, headers=HEADERS, impersonate="chrome124", timeout=15)
print("Status:", resp.status_code)
print("Content-Type:", resp.headers.get("content-type"))
print("First 1500 chars:")
print(resp.text[:1500])

try:
    data = resp.json()
    print("\n✅ Це JSON! Структура верхнього рівня:")
    if isinstance(data, dict):
        print(list(data.keys()))
    elif isinstance(data, list):
        print(f"список з {len(data)} елементів, перший:")
        print(json.dumps(data[0], ensure_ascii=False, indent=2)[:1500] if data else "порожньо")
except ValueError:
    print("\n❌ Не JSON.")
