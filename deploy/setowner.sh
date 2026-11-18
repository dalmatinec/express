#!/usr/bin/env bash
# Меняет ID владельца/админа во всех .env на этом сервере и перезапускает сервисы этих ботов.
#   bash setowner.sh          заменить прежние ID на новый
#   bash setowner.sh --add    не заменять, а добавить новый ID к уже стоящим
# Свой ID узнайте у @userinfobot. Перед правкой рядом сохраняется копия .env.bak-<время>.
set -uo pipefail
read -ra DIRS <<< "${SCAN_DIRS:-/opt /root /home /srv}"
UNITS="${UNIT_DIR:-/etc/systemd/system}"
MODE=replace; [ "${1:-}" = "--add" ] && MODE=add
KEY='^(OWNER|ADMIN|SUPER)[A-Za-z_]*(ID|IDS)='

read -rp "Новый Telegram ID (только цифры): " NEW
[[ "$NEW" =~ ^[0-9]{5,15}$ ]] || { echo "ID должен состоять из 5-15 цифр. Ничего не изменено."; exit 1; }

mapfile -t FILES < <(find "${DIRS[@]}" -maxdepth 3 -name .env \
    -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/venv/*' 2>/dev/null | sort)
HITS=()
for f in "${FILES[@]}"; do grep -E "$KEY" "$f" | grep -qv GROUP && HITS+=("$f"); done
[ "${#HITS[@]}" -gt 0 ] || { echo "Не нашёл ни одного .env со строками OWNER*/ADMIN*."; exit 0; }

echo "Найдено ботов: ${#HITS[@]}. Сейчас там:"
for f in "${HITS[@]}"; do echo "== $f"; grep -E "$KEY" "$f" | grep -v GROUP | sed 's/^/   /'; done
echo
[ "$MODE" = add ] && echo "Режим: ДОБАВИТЬ $NEW к существующим." || echo "Режим: ЗАМЕНИТЬ на $NEW."
read -rp "Применить ко всем? (y/N) " OK
[ "$OK" = "y" ] || { echo "Отменено."; exit 0; }

service_of() {
    local dir="$1" f
    for f in "$UNITS"/*.service; do
        [ -f "$f" ] || continue
        if grep -qE "^(WorkingDirectory|ExecStart)=.*$dir(/|\s|$)" "$f"; then basename "$f" .service; return; fi
    done
}

for f in "${HITS[@]}"; do
    dir=$(dirname "$f")
    cp -p "$f" "$f.bak-$(date +%Y%m%d_%H%M%S)"
    if [ "$MODE" = add ]; then
        # дописываем через запятую, если такого ID там ещё нет
        sed -i -E "/GROUP/! { /$KEY/ { /(=|,)$NEW(,|$)/! s/=(.+)$/=\\1,$NEW/; /=$/ s/=$/=$NEW/ } }" "$f"
    else
        sed -i -E "/GROUP/! s/($KEY).*/\\1$NEW/" "$f"   # ADMIN_GROUP_ID (id чата) не трогаем
    fi
    svc=$(service_of "$dir")
    if [ -n "$svc" ]; then
        systemctl restart "$svc" && echo "OK  $dir -> перезапущен $svc"
    elif [ -f "$dir/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
        (cd "$dir" && docker compose up -d --force-recreate >/dev/null 2>&1) && echo "OK  $dir -> docker пересоздан"
    else
        echo "!!  $dir -> .env изменён, но сервис не найден: перезапустите бота вручную"
    fi
done

echo
echo "Боты, где ID прописан в config.py (правьте вручную или скажите мне):"
grep -rInE --include=config.py -E '(OWNER|ADMIN|SUPER)[A-Za-z_]*(ID|IDS)\s*=' "${DIRS[@]}" \
    --exclude-dir=.venv --exclude-dir=venv --exclude-dir=node_modules 2>/dev/null | cut -c1-140 || true
