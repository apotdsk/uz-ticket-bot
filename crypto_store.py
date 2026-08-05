"""
Шифрування файлів стану (selected_routes.json, state.json) для сценарію
"публічний код-репозиторій, але дані користувачів лишаються приватними".

Симетричне шифрування (Fernet = AES128-CBC + HMAC) ключем ENCRYPTION_KEY
(GitHub secret локально в .env), який НІКОЛИ не потрапляє в git. Без
цього ключа вміст закомічених файлів — нечитабельний шифротекст, навіть
якщо репозиторій публічний і вся git-історія видна всім назавжди.

Якщо ENCRYPTION_KEY не заданий — файли читаються/пишуться як звичайний
текст (як і раніше), нічого не ламається для тих, хто цим не користується.

Згенерувати новий ключ (ОДИН раз, зберегти і як GitHub secret, і в
локальний .env — той самий ключ в обох місцях):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Optional[Fernet]:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        return None
    return Fernet(key.encode())


def read_text(path: Path, default: str) -> str:
    """Читає файл, розшифровуючи його, якщо ENCRYPTION_KEY заданий."""
    if not path.exists():
        return default
    raw = path.read_bytes()
    f = _fernet()
    if f is None:
        return raw.decode("utf-8")
    try:
        return f.decrypt(raw).decode("utf-8")
    except InvalidToken:
        # Файл ще не зашифрований (напр. локальні дані з часів до
        # увімкнення ENCRYPTION_KEY) — читаємо як є, наступний запис уже
        # зашифрує.
        return raw.decode("utf-8")


def write_text(path: Path, text: str) -> None:
    """Пише файл, шифруючи вміст, якщо ENCRYPTION_KEY заданий."""
    data = text.encode("utf-8")
    f = _fernet()
    if f is not None:
        data = f.encrypt(data)
    path.write_bytes(data)
