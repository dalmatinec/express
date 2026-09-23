"""
Конфигурация проекта.
Значения берутся из переменных окружения (или файла .env), иначе — дефолты ниже.
"""

import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
# Супер-админы: один или несколько ID через запятую, например 111,222,333
SUPER_ADMIN_IDS = [
    int(part) for part in os.getenv("SUPER_ADMIN_ID", "123456789").replace(" ", "").split(",") if part
]
DATABASE_PATH = os.getenv("DATABASE_PATH", "database.db")

# Группа по умолчанию (можно сменить командой /setgroup, значение из БД важнее)
DEFAULT_GROUP_ID = int(os.getenv("GROUP_ID", "0")) or None

# ==================== АНТИФЛУД / АНТИСПАМ ====================
# Действует только на пользователей в личке бота. В группе ограничений нет.

FLOOD_MIN_INTERVAL = float(os.getenv("FLOOD_MIN_INTERVAL", "1.5"))  # мин. пауза между сообщениями, сек
FLOOD_WINDOW = int(os.getenv("FLOOD_WINDOW", "60"))                # окно подсчёта, сек
FLOOD_MAX_MESSAGES = int(os.getenv("FLOOD_MAX_MESSAGES", "10"))    # макс. сообщений за окно
FLOOD_MUTE_SECONDS = int(os.getenv("FLOOD_MUTE_SECONDS", "300"))   # мут при превышении, сек
DUPLICATE_WINDOW = int(os.getenv("DUPLICATE_WINDOW", "60"))        # повтор одного текста игнорируется, сек
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "2000"))        # макс. длина сообщения
