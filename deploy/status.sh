#!/usr/bin/env bash
# Красивый цветной список всех ботов на сервере: жив ли токен и запущен ли сам бот.
# Токены не печатаются. Запуск: bash status.sh   (от root, на каждом сервере)
set -uo pipefail
RE='[0-9]{8,11}:[A-Za-z0-9_-]{35}'
read -ra DIRS <<< "${SCAN_DIRS:-/opt /root /home /srv /var/www}"
UNITS="${UNIT_DIR:-/etc/systemd/system}"
EXCL=(--exclude-dir=.venv --exclude-dir=venv --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=site-packages --exclude-dir=backups --exclude-dir=__pycache__ --exclude-dir='*.bak' --exclude-dir='*.bak-*' --exclude-dir=_to_delete)

if [ -n "${NO_COLOR:-}" ]; then G=; Y=; R=; D=; B=; N=; else
    G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[90m'; B=$'\e[1m'; N=$'\e[0m'; fi

PROXY=
if (exec 3<>/dev/tcp/127.0.0.1/1080) 2>/dev/null; then PROXY=1; fi

tg() {  # getMe напрямую, а если не вышло и есть туннель на 1080 - через него
    local out
    out=$(curl -s --max-time 8 "https://api.telegram.org/bot$1/getMe")
    if [ -z "$out" ] && [ -n "$PROXY" ]; then
        out=$(curl -s --max-time 12 --socks5-hostname 127.0.0.1:1080 "https://api.telegram.org/bot$1/getMe")
    fi
    echo "$out"
}

service_of() {
    local dir="$1" f
    for f in "$UNITS"/*.service; do
        [ -f "$f" ] || continue
        if grep -qE "^(WorkingDirectory|ExecStart)=.*$dir(/|\s|$)" "$f"; then basename "$f" .service; return; fi
    done
}

declare -A seen
ok=0; warn=0; bad=0; total=0
echo
echo "${B}  Сервер: $(hostname)${N}   ${D}$(date '+%d.%m %H:%M')${N}"
echo "${D}  ──────────────────────────────────────────────────────────────${N}"

emit() {  # $1 имя, $2 юзернейм, $3 состояние, $4 цвет, $5 пояснение
    printf '  %s●%s %-16s %-24s %s%s%s\n' "$4" "$N" "$1" "$2" "$4" "$3" "$N"
    [ -n "${5:-}" ] && printf '    %s%s%s\n' "$D" "$5" "$N"
}

rows=$(grep -rIEo "${EXCL[@]}" --include=.env --include=*.py --include=*.json --include=*.ini \
        --include=*.yaml --include=*.yml --include=*.cfg --include=*.toml \
        "$RE" "${DIRS[@]}" 2>/dev/null | sort -u)

while IFS=: read -r file id rest; do
    [ -n "$file" ] || continue
    token="$id:$rest"
    [ -n "${seen[$token]:-}" ] && continue
    seen[$token]=1
    dir=$(dirname "$file")
    while [ "$dir" != "/" ] && [ ! -f "$dir/.env" ] && [ ! -d "$dir/bot" ]; do dir=$(dirname "$dir"); done
    [ "$dir" = "/" ] && dir=$(dirname "$file")
    name=$(basename "$dir")
    svc=$(service_of "$dir")
    out=$(tg "$token")
    total=$((total+1))
    if echo "$out" | grep -q '"ok":true'; then
        user="@$(echo "$out" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)"
        if [ -n "$svc" ]; then
            if [ "$(systemctl is-active "$svc" 2>/dev/null)" = "active" ]; then
                emit "$name" "$user" "работает" "$G" ""; ok=$((ok+1))
            else
                emit "$name" "$user" "ОСТАНОВЛЕН" "$Y" "токен живой, сервис $svc не запущен: systemctl restart $svc"; warn=$((warn+1))
            fi
        elif pgrep -f "$dir" >/dev/null 2>&1; then
            emit "$name" "$user" "работает (без сервиса)" "$Y" "после перезагрузки не поднимется, нужен автозапуск"; warn=$((warn+1))
        else
            emit "$name" "$user" "НЕ ЗАПУЩЕН" "$Y" "токен живой, но процесса нет и сервиса тоже"; warn=$((warn+1))
        fi
    elif echo "$out" | grep -q 'Unauthorized'; then
        emit "$name" "бот №$id" "токен мёртвый" "$R" "нужен новый токен: bash inventory.sh --fix"; bad=$((bad+1))
    else
        emit "$name" "бот №$id" "нет ответа Telegram" "$D" "сеть или туннель; повторите через минуту"; warn=$((warn+1))
    fi
done <<< "$rows"

if command -v docker >/dev/null 2>&1; then
    for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null); do
        for token in $(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" 2>/dev/null | grep -Eo "$RE" | sort -u); do
            [ -n "${seen[$token]:-}" ] && continue
            seen[$token]=1; total=$((total+1))
            out=$(tg "$token"); st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
            if echo "$out" | grep -q '"ok":true'; then
                user="@$(echo "$out" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)"
                if [ "$st" = "running" ]; then emit "$c (docker)" "$user" "работает" "$G" ""; ok=$((ok+1))
                else emit "$c (docker)" "$user" "ОСТАНОВЛЕН" "$Y" "docker start $c"; warn=$((warn+1)); fi
            elif echo "$out" | grep -q Unauthorized; then emit "$c (docker)" "бот №${token%%:*}" "токен мёртвый" "$R" ""; bad=$((bad+1))
            else emit "$c (docker)" "бот №${token%%:*}" "нет ответа Telegram" "$D" ""; warn=$((warn+1)); fi
        done
    done
fi

echo "${D}  ──────────────────────────────────────────────────────────────${N}"
printf '  Всего: %s%s%s   %sработают: %s%s   %sвнимание: %s%s   %sмёртвые: %s%s\n\n' "$B" "$total" "$N" "$G" "$ok" "$N" "$Y" "$warn" "$N" "$R" "$bad" "$N"
