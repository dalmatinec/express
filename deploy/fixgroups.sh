#!/usr/bin/env bash
# Возвращает ADMIN_GROUP_ID (id чата), который старая версия setowner.sh ошибочно заменила на личный ID.
# Берёт значение из самой ранней копии .env.bak-* рядом с файлом и перезапускает бота.
set -uo pipefail
UNITS=/etc/systemd/system
service_of() {
    local dir="$1" f
    for f in "$UNITS"/*.service; do
        [ -f "$f" ] || continue
        if grep -qE "^(WorkingDirectory|ExecStart)=.*$dir(/|\s|$)" "$f"; then basename "$f" .service; return; fi
    done
}
for f in $(find /opt /root /home /srv -maxdepth 3 -name .env -not -path '*/node_modules/*' 2>/dev/null | sort); do
    grep -q '^ADMIN_GROUP_ID=' "$f" || continue
    bak=$(ls -tr "$f".bak-* 2>/dev/null | head -1); [ -n "$bak" ] || continue
    old=$(grep '^ADMIN_GROUP_ID=' "$bak" | head -1); now=$(grep '^ADMIN_GROUP_ID=' "$f" | head -1)
    [ -n "$old" ] && [ "$old" != "$now" ] || continue
    sed -i "s|^ADMIN_GROUP_ID=.*|$old|" "$f"
    dir=$(dirname "$f"); svc=$(service_of "$dir")
    if [ -n "$svc" ]; then systemctl restart "$svc"; echo "ВЕРНУЛ $old в $dir (перезапущен $svc)"
    elif [ -f "$dir/docker-compose.yml" ]; then (cd "$dir" && docker compose up -d --force-recreate >/dev/null 2>&1); echo "ВЕРНУЛ $old в $dir (docker)"
    else echo "ВЕРНУЛ $old в $dir (перезапустите вручную)"; fi
done
