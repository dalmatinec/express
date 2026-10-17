"""Настройки из .env. Тут только то, что нельзя поменять в самом боте."""
import logging
import os
import re
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Almaty"


def parse_tz(value: str) -> tzinfo | None:
    """Asia/Almaty, а ещё просто смещение: +5, UTC+5, GMT+05:00, -3. Непонятное -> None."""
    value = value.strip()
    m = re.fullmatch(r"(?i)(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", value)
    if m:
        hours, minutes = int(m[2]), int(m[3] or 0)
        if hours > 14 or minutes > 59:
            return None
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(-delta if m[1] == "-" else delta)
    if value.upper() in ("UTC", "GMT"):
        return timezone.utc
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    owner_ids: frozenset[int]
    data_dir: Path
    tz_name: str = DEFAULT_TZ
    log_level: str = "INFO"

    @property
    def tz(self) -> tzinfo:
        return parse_tz(self.tz_name) or ZoneInfo(DEFAULT_TZ)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def backup_path(self) -> Path:
        # всегда один файл, новый бэкап перезаписывает старый
        return self.data_dir / "backup.zip"


def load_config() -> Config:
    _load_dotenv(Path(".env"))
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN, смотрите .env.example")
    raw = os.environ.get("OWNER_IDS", "")
    try:
        owners = frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)
    except ValueError:
        raise SystemExit("OWNER_IDS должен быть из чисел через запятую")
    if not owners:
        raise SystemExit("Не задан OWNER_IDS: впишите свой Telegram ID")
    tz_name = os.environ.get("TZ_NAME", DEFAULT_TZ).strip() or DEFAULT_TZ
    if parse_tz(tz_name) is None:
        # не падаем из-за пояса, просто берём Алматы и пишем в лог
        logging.getLogger(__name__).warning("Не понял часовой пояс %r, беру %s", tz_name, DEFAULT_TZ)
        tz_name = DEFAULT_TZ
    return Config(
        bot_token=token,
        owner_ids=owners,
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        tz_name=tz_name,
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
