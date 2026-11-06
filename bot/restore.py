"""Восстановление из архива на сервере (бот должен быть остановлен):
    python -m bot.restore data/backups/backup_20260101_040000.zip
    python -m bot.restore архив.zip --force     восстановить, даже если SECRET_KEY не подошёл (токены зеркал потеряются)
"""
import sys
from pathlib import Path

from .backup import restore_files
from .config import load_config


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--force"]
    if len(args) != 1:
        raise SystemExit(__doc__)
    config = load_config()
    try:
        manifest = restore_files(Path(args[0]), config.db_path, config.media_dir, config.secret_key,
                                 force="--force" in sys.argv)
    except ValueError as e:
        raise SystemExit(f"Не восстановлено: {e}.\nУкажите прежний SECRET_KEY в .env или добавьте --force.")
    when = manifest.get("created", "?")
    print(f"Готово (архив от {when}). Запустите бота: python -m bot")


if __name__ == "__main__":
    main()
