"""Шифрование токенов клонов. Без SECRET_KEY клоны просто недоступны."""
from __future__ import annotations

import base64
import hashlib


class TokenCipher:
    def __init__(self, secret: str) -> None:
        # Импорт здесь: пока клонов нет, библиотека не занимает память.
        from cryptography.fernet import Fernet

        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)

    def encrypt(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt(self, blob: bytes) -> str:
        return self._fernet.decrypt(bytes(blob)).decode()
