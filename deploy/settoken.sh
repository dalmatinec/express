#!/usr/bin/env bash
# Меняет токен бота в .env и перезапускает его сервис. Токен вводится скрытно и сначала проверяется в Telegram.
# Запуск: bash settoken.sh /opt/имя_бота      (или путь к самому .env)
set -euo pipefail
RE='^[0-9]{8,11}:[A-Za-z0-9_-]{35}$'
ARG="${1:-}"; [ -n "$ARG" ] || { echo "Укажите папку бота: bash settoken.sh /opt/имя"; exit 1; }
if [ -d "$ARG" ]; then DIR="${ARG%/}"; ENVF="$DIR/.env"; else ENVF="$ARG"; DIR="$(dirname "$ARG")"; fi
[ -f "$ENVF" ] || { echo "Нет файла $ENVF. Токен в этом боте лежит не в .env, правьте вручную."; exit 1; }

# какая переменная хранит токен
VAR="${2:-}"
if [ -z "$VAR" ]; then
    VAR=$(grep -E '^[A-Za-z_]*(TOKEN)[A-Za-z_]*=' "$ENVF" | head -1 | cut -d= -f1 || true)
fi
[ -n "$VAR" ] || { echo "В $ENVF нет строки с TOKEN. Покажите её: grep -i token $ENVF | cut -c1-30"; exit 1; }
echo "Бот: $DIR   (переменная $VAR)"

read -rsp "Новый токен: " T; echo
T="$(echo -n "$T" | tr -d ' \r\n"'"'"'')"
[[ "$T" =~ $RE ]] || { echo "Это не похоже на токен (формат 123456789:AA...). Ничего не изменено."; exit 1; }
OUT=$(curl -s --max-time 10 "https://api.telegram.org/bot$T/getMe" || true)
echo "$OUT" | grep -q '"ok":true' || { echo "Telegram не принимает этот токен. Ничего не изменено."; exit 1; }
echo "Токен принят: @$(echo "$OUT" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)"

cp -p "$ENVF" "$ENVF.bak-$(date +%Y%m%d_%H%M%S)"; chmod 600 "$ENVF".bak-* 2>/dev/null || true
sed -i "s|^$VAR=.*|$VAR=$T|" "$ENVF"
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
