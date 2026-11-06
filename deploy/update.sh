#!/usr/bin/env bash
# Безопасное обновление: bash /opt/<имя>/deploy/update.sh
# Порядок: бэкап базы -> git pull -> зависимости -> перезапуск. Если бот не поднялся, возвращаем прежнюю версию.
# Имя сервиса = имя папки (/opt/mango -> сервис mango).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="$(basename "$DIR")"
cd "$DIR"

OLD="$(git rev-parse HEAD)"
STAMP="$(date +%Y%m%d_%H%M%S)"
SAFE="$DIR/data/pre-update_$STAMP"

echo "==> Копия данных перед обновлением: $SAFE"
mkdir -p "$SAFE"
systemctl stop "$NAME"
for f in data/*.db data/*.db-wal data/*.db-shm; do
    [ -e "$f" ] && cp -p "$f" "$SAFE/"
done
cp -p .env "$SAFE/.env"

rollback() {
    echo "!! Бот не запустился после обновления. Возвращаю версию ${OLD:0:7}"
    systemctl stop "$NAME" || true
    git reset --hard "$OLD"
    for f in "$SAFE"/*.db "$SAFE"/*.db-wal "$SAFE"/*.db-shm; do
        [ -e "$f" ] && cp -p "$f" "data/$(basename "$f")"
    done
    .venv/bin/pip install -q -r requirements.txt || true
    chown -R "$NAME:$NAME" data
    systemctl start "$NAME"
    echo "Откат выполнен. Причину смотрите: journalctl -u $NAME -n 50"
    exit 1
}

echo "==> Скачиваю обновление"
git pull --ff-only || { systemctl start "$NAME"; echo "git pull не удался, бот запущен на прежней версии"; exit 1; }
.venv/bin/pip install -q -r requirements.txt || rollback
chown -R "$NAME:$NAME" data

echo "==> Запускаю $NAME"
systemctl start "$NAME"
sleep 8
if ! systemctl is-active --quiet "$NAME"; then rollback; fi
sleep 7
systemctl is-active --quiet "$NAME" || rollback  # не упал ли сразу после старта

# храним три последние копии
ls -1dt data/pre-update_* 2>/dev/null | tail -n +4 | xargs -r rm -rf
echo "Готово: $(git log -1 --format='%h %s')"
