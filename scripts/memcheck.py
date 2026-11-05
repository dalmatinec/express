"""Сколько памяти занимает каждый слой. Каждый замер идёт в отдельном процессе."""
from __future__ import annotations

import os
import subprocess
import sys

RSS = (
    "from pathlib import Path;"
    "print(next(int(l.split()[1]) for l in Path('/proc/self/status').read_text().splitlines()"
    " if l.startswith('VmRSS:')))"
)

USE = """
import asyncio, json
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.methods import SendMessage, RestrictChatMember, GetUpdates, BanChatMember
from aiogram.types import (Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions,
    ChatMemberOwner, User)
for i in range(50):
    u = Update.model_validate({'update_id': i, 'message': {'message_id': 1, 'date': 1,
        'chat': {'id': -1, 'type': 'supergroup', 'title': 't'},
        'from': {'id': 1, 'is_bot': False, 'first_name': 'x'}, 'text': '/ban 1 hi',
        'reply_to_message': {'message_id': 2, 'date': 1, 'chat': {'id': -1, 'type': 'supergroup'},
            'from': {'id': 2, 'is_bot': False, 'first_name': 'y'}, 'text': 'z'}}})
    m = SendMessage(chat_id=-1, text='x', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='a', callback_data='b')]]))
    d = m.model_dump(warnings=False)
    RestrictChatMember(chat_id=-1, user_id=1, permissions=ChatPermissions(can_send_messages=False)).model_dump(warnings=False)
    BanChatMember(chat_id=-1, user_id=1).model_dump(warnings=False)
    GetUpdates(offset=1, timeout=1).model_dump(warnings=False)
    Update.model_validate({'update_id': 1, 'callback_query': {'id': '1', 'chat_instance': 'c',
        'from': {'id': 1, 'is_bot': False, 'first_name': 'x'}, 'data': 'a'}})
    Update.model_validate({'update_id': 1, 'my_chat_member': {
        'chat': {'id': -1, 'type': 'supergroup'}, 'from': {'id': 1, 'is_bot': False, 'first_name': 'x'},
        'date': 1, 'old_chat_member': {'status': 'left', 'user': {'id': 1, 'is_bot': True, 'first_name': 'x'}},
        'new_chat_member': {'status': 'member', 'user': {'id': 1, 'is_bot': True, 'first_name': 'x'}}}})
assert u.message.reply_to_message.from_user.id == 2 and d['reply_markup'] is not None
"""

LEAN = "import bot.lean; assert bot.lean.install()\n"
BASE = "import bot.app, bot.runtime\n"

CASES = {
    "обычная загрузка": BASE,
    "обычная загрузка + работа": BASE + USE,
    "экономная загрузка": LEAN + BASE,
    "экономная загрузка + работа": LEAN + BASE + USE,
}

ENVS = {"": {}}

for prefix, extra in ENVS.items():
    for name, code in CASES.items():
        result = subprocess.run(
            [sys.executable, "-c", f"{code}\n{RSS}"],
            capture_output=True, text=True, cwd=".", env={**os.environ, **extra},
        )
        label = f"{prefix}{name}"
        if result.returncode:
            print(f"{label:52} ОШИБКА: {result.stderr.strip().splitlines()[-1][:120]}")
        else:
            print(f"{label:52} {int(result.stdout.strip().splitlines()[-1]) / 1024:6.1f} МБ")
