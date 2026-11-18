#!/usr/bin/env bash
# Полностью убирает ботов с сервера: сервис, контейнеры Docker, процессы и папку (с .env, базой, медиа, логами).
#   bash retire.sh /root/auto /root/kara            показать, что будет удалено (ничего не трогает)
#   bash retire.sh --do /root/auto /root/kara       удалить
# Защита: живой бот (токен принимает Telegram) не удаляется без --force.
# nginx, сертификаты и cron скрипт только показывает: их убирают вручную, чтобы не задеть чужое.
set -uo pipefail
UNITS="${UNIT_DIR:-/etc/systemd/system}"
DO=0; FORCE=0; DIRS=()
for a in "$@"; do case "$a" in --do) DO=1;; --force) FORCE=1;; *) DIRS+=("${a%/}");; esac; done
[ "${#DIRS[@]}" -gt 0 ] || { sed -n '2,8p' "$0"; exit 1; }

token_alive() {  # 0 если в .env/config.py есть токен, который принимает Telegram
    local d="$1" t
    t=$(grep -rhoE --include=.env --include=config.py '[0-9]{8,11}:[A-Za-z0-9_-]{35}' "$d" 2>/dev/null | head -1)
    [ -n "$t" ] || return 1
    curl -s --max-time 10 "https://api.telegram.org/bot$t/getMe" | grep -q '"ok":true'
}

units_of() {
    local d="$1" f
    for f in "$UNITS"/*.service "$UNITS"/*.timer; do
        [ -f "$f" ] || continue
        grep -qE "^(WorkingDirectory|ExecStart)=.*$d(/|\s|$)" "$f" && basename "$f"
    done
}

for d in "${DIRS[@]}"; do
    echo "=================================================================="
    case "$d" in ""|/|/root|/opt|/home|/etc|/var|/usr|/srv|/boot) echo "ОТКАЗ: $d слишком общий путь."; continue;; esac
    [ -d "$d" ] || { echo "Папки $d нет, пропускаю."; continue; }
    echo "БОТ: $d   ($(du -sh "$d" 2>/dev/null | cut -f1))"
    if token_alive "$d" && [ "$FORCE" = 0 ]; then
        echo "  ОТКАЗ: у этого бота ЖИВОЙ токен. Это точно тот бот? Если да, добавьте --force."
        continue
    fi
    mapfile -t U < <(units_of "$d")
    echo "  сервисы systemd: ${U[*]:-нет}"
    COMPOSE=""; for c in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do [ -f "$d/$c" ] && COMPOSE="$c"; done
    [ -n "$COMPOSE" ] && echo "  docker compose: $COMPOSE (контейнеры, образы и тома будут удалены)"
    PROCS=$(pgrep -af "$d" | grep -v "retire.sh" | cut -c1-110)
    [ -n "$PROCS" ] && echo "  процессы: $PROCS"
    REFS=$(grep -rlsF "$d" /etc/nginx /etc/cron.d /etc/logrotate.d /var/spool/cron 2>/dev/null | head)
    [ -n "$REFS" ] && echo "  ВРУЧНУЮ проверить, ссылаются на папку: $REFS"
    crontab -l 2>/dev/null | grep -F "$d" | sed 's/^/  ВРУЧНУЮ убрать из crontab: /'

    [ "$DO" = 1 ] || continue
    for u in "${U[@]:-}"; do [ -n "$u" ] && { systemctl disable --now "$u" 2>/dev/null; rm -f "$UNITS/$u"; echo "  - удалён $u"; }; done
    rm -rf "$UNITS"/*.wants/"${U[0]:-__none__}" 2>/dev/null
    [ -n "$COMPOSE" ] && (cd "$d" && docker compose down -v --rmi local --remove-orphans 2>&1 | tail -2)
    for sig in TERM KILL; do  # свои процессы (сам скрипт и его родитель) не трогаем
        for pid in $(pgrep -f "$d" 2>/dev/null); do
            [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ] && kill -"$sig" "$pid" 2>/dev/null
        done
        sleep 1
    done
    rm -rf "$d" && echo "  - папка удалена"
done
[ "$DO" = 1 ] && systemctl daemon-reload && systemctl reset-failed 2>/dev/null
echo "=================================================================="
[ "$DO" = 1 ] && echo "Готово." || echo "Это был просмотр, ничего не удалено. Для удаления добавьте --do."
