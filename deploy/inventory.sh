#!/usr/bin/env bash
# Находит на этом сервере все токены ботов и показывает, какие живы, а какие нет (Unauthorized).
# Токены не печатаются, только номер бота. Запуск: bash inventory.sh   (от root, на каждом сервере)
set -uo pipefail
RE='[0-9]{8,11}:[A-Za-z0-9_-]{35}'
read -ra DIRS <<< "${SCAN_DIRS:-/opt /root /home /srv /var/www}"
UNITS="${UNIT_DIR:-/etc/systemd/system}"
EXCL=(--exclude-dir=.venv --exclude-dir=venv --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=site-packages --exclude-dir=backups --exclude-dir=__pycache__)

service_of() {  # сервис бота из этой папки (разовые oneshot-проверки пропускаем)
    local dir="$1" f first=""
    for f in "$UNITS"/*.service; do
        [ -f "$f" ] || continue
        if grep -qE "^(WorkingDirectory|ExecStart)=.*$dir(/|\s|$)" "$f"; then
            grep -qE '^Type=oneshot' "$f" && continue
            basename "$f" .service; return
        fi
    done
}

check() {  # статус токена по ответу Telegram
    local out
    out=$(curl -s --max-time 10 "https://api.telegram.org/bot$1/getMe")
    if echo "$out" | grep -q '"ok":true'; then
        echo "ЖИВОЙ    @$(echo "$out" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)"
    elif echo "$out" | grep -q 'Unauthorized'; then echo "МЁРТВЫЙ  (Unauthorized)"
    else echo "?        (нет ответа Telegram)"; fi
}

declare -A seen
DEAD=()
echo "Сервер: $(hostname)"
echo "-----------------------------------------------------------------"
while IFS=: read -r file id rest; do
    token="$id:$rest"
    [ -n "${seen[$token]:-}" ] && continue
    seen[$token]=1
    dir=$(dirname "$file")
    while [ "$dir" != "/" ] && [ ! -f "$dir/.env" ] && [ ! -d "$dir/bot" ]; do dir=$(dirname "$dir"); done
    [ "$dir" = "/" ] && dir=$(dirname "$file")  # токен лежит в config.py без .env: папка = где файл
    svc=$(service_of "$dir")
    st="$(check "$token")"
    printf '%s\n   папка: %s | файл: %s | сервис: %s | бот №%s\n' "$st" "$dir" "$file" "${svc:--}" "$id"
    case "$st" in МЁРТВЫЙ*) DEAD+=("$dir");; esac
done < <(grep -rIEo "${EXCL[@]}" --include=.env --include='*.env' --include=*.py --include=*.json --include=*.ini \
            --include=*.yaml --include=*.yml --include=*.cfg --include=*.toml --include=*.txt \
            "$RE" "${DIRS[@]}" 2>/dev/null | sort -u)

if command -v docker >/dev/null 2>&1; then
    for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null); do
        for token in $(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" 2>/dev/null | grep -Eo "$RE" | sort -u); do
            [ -n "${seen[$token]:-}" ] && continue
            seen[$token]=1
            printf '%s\n   docker-контейнер: %s | бот №%s\n' "$(check "$token")" "$c" "${token%%:*}"
        done
    done
fi
echo "-----------------------------------------------------------------"
if [ "${1:-}" = "--fix" ] && [ "${#DEAD[@]}" -gt 0 ]; then
    echo "Мёртвых ботов: ${#DEAD[@]}. Для каждого введите НОВЫЙ токен или просто Enter, чтобы пропустить."
    for d in $(printf '%s\n' "${DEAD[@]}" | sort -u); do
        echo; echo "=== $d"
        bash "$(dirname "$0")/settoken.sh" "$d" </dev/tty || true
    done
else
    echo "Мёртвых: ${#DEAD[@]}. Заменить токены по очереди: bash $0 --fix"
fi
