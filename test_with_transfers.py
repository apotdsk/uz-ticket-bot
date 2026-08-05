"""
Тест: чи дає with_transfers=1 непорожній масив "with_transfer", і яка в
нього структура (щоб потім правильно дописати extract_availability для
трипів із пересадкою).

Запуск: python test_with_transfers.py
"""

import json
import uuid

from curl_cffi import requests

URL = "https://app.uz.gov.ua/api/v3/trips"
HEADERS = {
    "accept": "application/json",
    "accept-language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://booking.uz.gov.ua",
    "referer": "https://booking.uz.gov.ua/",
    "x-client-locale": "uk",
    "x-session-id": str(uuid.uuid4()),
    "x-user-agent": "UZ/2 Web/1 User/guest",
}

# Підставте маршрут/дату, де ви точно знаєте, що прямих потягів мало/немає —
# на такому найімовірніше буде непорожній with_transfer.
PARAMS = {
    "station_from_id": "2218500",
    "station_to_id": "2218000",
    "with_transfers": 1,
    "date": "2026-08-09",
}

resp = requests.get(URL, params=PARAMS, headers=HEADERS, impersonate="chrome124", timeout=15)
print("Status:", resp.status_code)

data = resp.json()
direct = data.get("direct", [])
with_transfer = data.get("with_transfer", [])
print(f"direct: {len(direct)} трипів, with_transfer: {len(with_transfer)} трипів")

if with_transfer:
    print("\nПерший трип із пересадкою (структура):")
    print(json.dumps(with_transfer[0], ensure_ascii=False, indent=2)[:2500])
else:
    print("\nwith_transfer порожній на цьому маршруті/даті — спробуйте інший "
          "маршрут (бажано такий, де ви точно знаєте, що прямих потягів "
          "немає, тільки з пересадкою).")
