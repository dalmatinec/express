"""Восстановление из файла бэкапа прямо на сервере (бот в это время должен быть остановлен):
    python -m postbot.restore /root/backup_20261017_0400.zip
"""
import sys
from pathlib import Path

from .config import load_config
from .services.backup import restore_files


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    config = load_config()
    try:
        restore_files(Path(sys.argv[1]), config.db_path, config.media_dir)
    except ValueError as e:
        raise SystemExit(f"Не получилось: {e}")
    print("Готово. Запускайте бота.")


if __name__ == "__main__":
    main()
