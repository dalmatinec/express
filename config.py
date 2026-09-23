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

# Реакция «доставлено»: ставится на сообщение клиента после пересылки в группу
# и на ответ в группе после доставки клиенту. Пусто — не ставить.
# Можно только эмодзи из стандартного списка реакций Telegram (👍 👌 🔥 ❤ 🙏 ✍ и т.п.).
DELIVERED_REACTION = os.getenv("DELIVERED_REACTION", "👍").strip()

# ==================== АНТИФЛУД / АНТИСПАМ ====================
# Действует только на пользователей в личке бота. В группе ограничений нет.
# Это значения по умолчанию — их можно менять в админ-панели («🛡 Антифлуд»).

FLOOD_MAX_MESSAGES = int(os.getenv("FLOOD_MAX_MESSAGES", "7"))     # макс. сообщений за окно
FLOOD_WINDOW = int(os.getenv("FLOOD_WINDOW", "5"))                 # окно подсчёта, сек
FLOOD_MUTE_SECONDS = int(os.getenv("FLOOD_MUTE_SECONDS", "300"))   # мут при превышении, сек
FLOOD_MIN_INTERVAL = int(os.getenv("FLOOD_MIN_INTERVAL", "0"))     # мин. пауза между сообщениями, сек (0 — выкл.)
DUPLICATE_WINDOW = int(os.getenv("DUPLICATE_WINDOW", "60"))        # повтор одного текста игнорируется, сек (0 — выкл.)
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "2000"))        # макс. длина сообщения
