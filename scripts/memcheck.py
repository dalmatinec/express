"""Сколько памяти занимает каждый слой. Каждый замер идёт в отдельном процессе."""
from __future__ import annotations

import subprocess
import sys

RSS = (
    "from pathlib import Path;"
    "print(next(int(l.split()[1]) for l in Path('/proc/self/status').read_text().splitlines()"
    " if l.startswith('VmRSS:')))"
)

DEFER = """
from pydantic._internal import _config
_orig = _config.ConfigWrapper.for_model
def _for_model(*a, **k):
    w = _orig(*a, **k)
    w.config_dict['defer_build'] = True
    return w
_config.ConfigWrapper.for_model = staticmethod(_for_model)
"""

PARSE = """
from aiogram.types import Update
for i in range(50):
    Update.model_validate({'update_id': i, 'message': {'message_id': 1, 'date': 1,
        'chat': {'id': -1, 'type': 'supergroup', 'title': 't'},
        'from': {'id': 1, 'is_bot': False, 'first_name': 'x'}, 'text': 'hi'}})
"""

CASES = {
    "голый python": "",
    "pydantic": "import pydantic",
    "aiohttp": "import aiohttp",
    "asyncpg": "import asyncpg",
    "uvloop": "import uvloop",
    "orjson": "import orjson",
    "cryptography (fernet)": "from cryptography.fernet import Fernet",
    "aiogram.types": "import aiogram.types",
    "aiogram целиком": "import aiogram",
    "бот": "import bot.app, bot.runtime",
    "бот + разбор сообщений": "import bot.app, bot.runtime\n" + PARSE,
    "defer_build: бот": DEFER + "import bot.app, bot.runtime",
    "defer_build: бот + разбор": DEFER + "import bot.app, bot.runtime\n" + PARSE,
}

for name, code in CASES.items():
    result = subprocess.run(
        [sys.executable, "-c", f"{code}\n{RSS}"], capture_output=True, text=True, cwd="."
    )
    if result.returncode:
        print(f"{name:32} ОШИБКА: {result.stderr.strip().splitlines()[-1][:120]}")
    else:
        print(f"{name:32} {int(result.stdout.strip().splitlines()[-1]) / 1024:6.1f} МБ")
