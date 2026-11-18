#!/usr/bin/env bash
# Меняет токен бота в .env и перезапускает его сервис. Токен вводится скрытно и сначала проверяется в Telegram.
# Запуск: bash settoken.sh /opt/имя_бота      (или путь к самому .env)
set -euo pipefail
RE='^[0-9]{8,11}:[A-Za-z0-9_-]{35}$'
ARG="${1:-}"; [ -n "$ARG" ] || { echo "Укажите папку бота: bash settoken.sh /opt/имя"; exit 1; }
if [ -d "$ARG" ]; then DIR="${ARG%/}"; ENVF="$DIR/.env"; else ENVF="$ARG"; DIR="$(dirname "$ARG")"; fi
MODE=env; VAR="${2:-}"
if [ -f "$ENVF" ]; then
    [ -n "$VAR" ] || VAR=$(grep -E '^[A-Za-z_]*(TOKEN)[A-Za-z_]*=' "$ENVF" | head -1 | cut -d= -f1 || true)
fi
if [ -z "$VAR" ]; then
    # нет .env с токеном: ищем токен прямо в коде (config.py и т.п.)
    ENVF=$(grep -rIlE --include=*.py --include=*.json --include=*.ini --include=*.yaml --include=*.yml --include=*.cfg --include=*.toml \
           --exclude-dir=.venv --exclude-dir=venv --exclude-dir=node_modules --exclude-dir=.git \
           '[0-9]{8,11}:[A-Za-z0-9_-]{35}' "$DIR" 2>/dev/null | head -1 || true)
    [ -n "$ENVF" ] || { echo "В $DIR не найден токен ни в .env, ни в файлах настроек. Правьте вручную."; exit 1; }
    MODE=code
fi
echo "Бот: $DIR   (файл $ENVF$([ "$MODE" = env ] && echo ", переменная $VAR"))"

read -rsp "Новый токен: " T; echo
T="$(echo -n "$T" | tr -d ' \r\n"'"'"'')"
[ -n "$T" ] && : || { echo "Пропущено."; exit 0; }
[[ "$T" =~ $RE ]] || { echo "Это не похоже на токен (формат 123456789:AA...). Ничего не изменено."; exit 1; }
OUT=$(curl -s --max-time 10 "https://api.telegram.org/bot$T/getMe" || true)
echo "$OUT" | grep -q '"ok":true' || { echo "Telegram не принимает этот токен. Ничего не изменено."; exit 1; }
echo "Токен принят: @$(echo "$OUT" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)"

cp -p "$ENVF" "$ENVF.bak-$(date +%Y%m%d_%H%M%S)"; chmod 600 "$ENVF".bak-* 2>/dev/null || true
if [ "$MODE" = env ]; then
    sed -i "s|^$VAR=.*|$VAR=$T|" "$ENVF"
else
    OLD=$(grep -oE '[0-9]{8,11}:[A-Za-z0-9_-]{35}' "$ENVF" | head -1)
    sed -i "s|$OLD|$T|g" "$ENVF"
fi
unset T

SVC=""
for f in "${UNIT_DIR:-/etc/systemd/system}"/*.service; do
    [ -f "$f" ] || continue
    if grep -qE "^(WorkingDirectory|ExecStart)=.*$DIR(/|\s|$)" "$f"; then SVC="$(basename "$f" .service)"; break; fi
done
if [ -n "$SVC" ]; then
    systemctl restart "$SVC"; sleep 7
    echo "Сервис $SVC перезапущен. Последние строки лога:"
    journalctl -u "$SVC" -n 6 --no-pager | cut -c1-150
else
    echo "Сервис для этой папки не найден. Перезапустите бота вручную (или docker compose restart)."
fi
