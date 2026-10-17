"""Настройки из .env. Тут только то, что нельзя поменять в самом боте."""
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    tz_name: str = "Asia/Almaty"
    log_level: str = "INFO"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

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
    tz_name = os.environ.get("TZ_NAME", "Asia/Almaty").strip() or "Asia/Almaty"
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SystemExit(f"Не знаю часовой пояс {tz_name}, пример: Asia/Almaty, Europe/Moscow")
    return Config(
        bot_token=token,
        owner_ids=owners,
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        tz_name=tz_name,
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
