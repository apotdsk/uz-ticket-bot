"""
Зберігання обраних маршрутів, тепер по КОЖНОМУ chat_id окремо (мультикористувацький
режим — кожен, хто пише боту, керує своїми маршрутами незалежно від інших).

Формат файлу:
    {
      "next_id": 5,
      "users": {
        "123456789": {"routes": [{"id": 3, "station_from_name": "...", ...}, ...]},
        "987654321": {"routes": [...]}
      }
    }

"next_id" — ГЛОБАЛЬНИЙ наскрізний лічильник (спільний для всіх користувачів),
який ЗАВЖДИ зростає і ніколи не повторюється — навіть якщо конкретний
користувач скидає всі свої маршрути. Це критично для дедублікації
сповіщень у state.py (ключ будується як route{id}|дата|id_рейсу): якби
номер міг повторитись, новий маршрут "успадкував" би стан старого
видаленого маршруту з тим самим номером, і бот мовчав би про вже наявні
місця, думаючи що вже сповіщав.

GIT-СИНХРОНІЗАЦІЯ (опційно, для сценарію "додав маршрут у Telegram,
вимкнув сервер, а GitHub Actions сам перевіряє й шле сповіщення"): якщо
в середовищі є GIT_SYNC=1, кожна зміна (add_route/remove_route) сама
комітить і пушить selected_routes.json (+ state.json, якщо чіпався) у
git-репозиторій, у якому лежить цей файл. Працює, лише якщо ця папка —
клон того самого GitHub-репо, з налаштованим доступом на push (PAT/SSH).
Без GIT_SYNC=1 усе працює як раніше, суто локально.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

import crypto_store

logger = logging.getLogger("storage")

STORAGE_FILE = Path(__file__).parent / "selected_routes.json"
REPO_DIR = Path(__file__).parent


def _load_raw() -> dict[str, Any]:
    raw_text = crypto_store.read_text(STORAGE_FILE, default="")
    if not raw_text:
        return {"next_id": 1, "users": {}}
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"next_id": 1, "users": {}}

    if isinstance(data, list):
        # Найстаріший формат (простий список без номерів користувачів) —
        # мігруємо під TELEGRAM_CHAT_ID з env, якщо він є, інакше відкидаємо
        # (це були тестові дані з часів однокористувацького бота).
        legacy_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        next_id = max((r.get("id", 0) for r in data), default=0) + 1
        users = {legacy_chat_id: {"routes": data}} if legacy_chat_id else {}
        return {"next_id": next_id, "users": users}

    if "users" not in data:
        # Проміжний однокористувацький формат {"next_id":.., "routes":[...]}
        legacy_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        routes = data.get("routes", [])
        users = {legacy_chat_id: {"routes": routes}} if legacy_chat_id else {}
        return {"next_id": data.get("next_id", 1), "users": users}

    data.setdefault("next_id", 1)
    data.setdefault("users", {})
    return data


def _save_raw(data: dict[str, Any]) -> None:
    crypto_store.write_text(STORAGE_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def load_routes(chat_id: int | str) -> list[dict[str, Any]]:
    data = _load_raw()
    return data["users"].get(str(chat_id), {}).get("routes", [])


def load_all_routes() -> list[dict[str, Any]]:
    """Список маршрутів УСІХ користувачів, кожен із вбудованим chat_id — для фонової перевірки."""
    data = _load_raw()
    result = []
    for chat_id_str, user_data in data["users"].items():
        for route in user_data.get("routes", []):
            result.append({**route, "chat_id": int(chat_id_str)})
    return result


def add_route(chat_id: int | str, route: dict[str, Any]) -> int:
    """Присвоює маршруту наступний вільний номер із ГЛОБАЛЬНОГО лічильника, зберігає, повертає номер."""
    data = _load_raw()
    route_id = data["next_id"]
    data["next_id"] = route_id + 1
    key = str(chat_id)
    data["users"].setdefault(key, {"routes": []})
    data["users"][key]["routes"].append({"id": route_id, **route})
    _save_raw(data)
    _git_sync(f"route: add #{route_id}")
    return route_id


def remove_route(chat_id: int | str, route_id: int) -> bool:
    data = _load_raw()
    key = str(chat_id)
    routes = data["users"].get(key, {}).get("routes", [])
    filtered = [r for r in routes if r["id"] != route_id]
    if len(filtered) == len(routes):
        return False
    data["users"][key]["routes"] = filtered
    _save_raw(data)
    _purge_state_for_route(route_id)
    _git_sync(f"route: remove #{route_id}")
    return True


def get_route(chat_id: int | str, route_id: int) -> Optional[dict[str, Any]]:
    for r in load_routes(chat_id):
        if r["id"] == route_id:
            return r
    return None


def _purge_state_for_route(route_id: int) -> None:
    """Прибирає зі state.json (дедублікація сповіщень) усі записи скинутого маршруту."""
    from state import load_state, save_state  # локальний імпорт, щоб уникнути циклічного

    prefix = f"route{route_id}|"
    state = load_state()
    filtered = {k: v for k, v in state.items() if not k.startswith(prefix)}
    if len(filtered) != len(state):
        save_state(filtered)


def _git_sync(commit_message: str) -> None:
    """
    Якщо GIT_SYNC=1 — комітить і пушить selected_routes.json (+ state.json,
    якщо є незакомічені зміни) у git-репозиторій. Best-effort: помилка
    (немає git, немає remote, конфлікт push) лише логується, не ламає
    основну дію (маршрут все одно збережено локально).
    """
    if os.environ.get("GIT_SYNC") != "1":
        return

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(args, cwd=REPO_DIR, capture_output=True, text=True)

    try:
        _run("git", "add", "selected_routes.json", "state.json")

        # Немає сенсу комітити/пушити, якщо насправді нічого не змінилось
        # (напр. state.json не чіпався).
        diff = _run("git", "diff", "--cached", "--quiet")
        if diff.returncode == 0:
            return

        commit = _run("git", "commit", "-m", commit_message)
        if commit.returncode != 0:
            logger.warning("git commit не вдався: %s", commit.stderr.strip())
            return

        pull = _run("git", "pull", "--rebase")
        if pull.returncode != 0:
            logger.warning(
                "git pull --rebase не вдався (можливий конфлікт із коммітами "
                "GitHub Actions): %s. Зміни закомічені локально, але НЕ запушені — "
                "розберіться вручну (git status / git rebase --abort і т.д.).",
                pull.stderr.strip(),
            )
            return

        push = _run("git", "push")
        if push.returncode != 0:
            logger.warning("git push не вдався: %s", push.stderr.strip())
        else:
            logger.info("Синхронізовано з git: %s", commit_message)
    except FileNotFoundError:
        logger.warning("git не знайдено в PATH — GIT_SYNC=1 виставлено, але синхронізація неможлива.")
