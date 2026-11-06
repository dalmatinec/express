#!/usr/bin/env bash
# Открывает веб-панель бота по обычной ссылке с HTTPS (без SSH-туннеля): bash /opt/<имя>/deploy/expose-panel.sh
# Адрес вида https://<имя>.<ip-сервера>.sslip.io (свой домен не нужен). Сертификат выдаёт Let's Encrypt сам.
# Свой домен: bash expose-panel.sh panel.example.com (его A-запись должна указывать на этот сервер).
# Панель по-прежнему слушает только 127.0.0.1, наружу её отдаёт ваш nginx (если он есть на 80/443) или Caddy.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="$(basename "$DIR")"
PORT="$(grep -E '^WEB_PORT=' "$DIR/.env" | cut -d= -f2)"
[ -n "${PORT:-}" ] || { echo "В $DIR/.env нет WEB_PORT. Сначала: bash $DIR/deploy/install.sh"; exit 1; }

if [ $# -ge 1 ]; then
    HOST="$1"
else
    IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
    [ -n "$IP" ] || IP="$(hostname -I | awk '{print $1}')"
    HOST="$NAME.${IP//./-}.sslip.io"
fi

PORT_BUSY=0
ss -ltn 2>/dev/null | grep -qE ':(80|443)\s' && PORT_BUSY=1

if [ "$PORT_BUSY" = 1 ] && ! pgrep -x caddy >/dev/null && command -v nginx >/dev/null 2>&1; then
    # Порты занимает nginx с другими сайтами: добавляем отдельный сайт для панели, остальные не трогаем
    echo "==> Подключаю панель к вашему nginx как отдельный сайт $HOST"
    command -v certbot >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq certbot python3-certbot-nginx; }
    CONF="/etc/nginx/sites-available/$NAME-panel.conf"
    cat > "$CONF" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $HOST;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        client_max_body_size 1m;
    }
}
NGINX
    ln -sf "$CONF" "/etc/nginx/sites-enabled/$NAME-panel.conf"
    if ! nginx -t; then
        rm -f "/etc/nginx/sites-enabled/$NAME-panel.conf"
        echo "Проверка nginx не прошла, новый сайт убран, остальное не тронуто."
        exit 1
    fi
    systemctl reload nginx
    if ! certbot --nginx -d "$HOST" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
        rm -f "/etc/nginx/sites-enabled/$NAME-panel.conf"
        systemctl reload nginx
        echo "Не удалось выпустить сертификат, сайт панели убран (без HTTPS пароли передавались бы открыто). Остальные сайты не тронуты."
        exit 1
    fi
    nginx -t && systemctl reload nginx
elif [ "$PORT_BUSY" = 1 ] && ! pgrep -x caddy >/dev/null; then
    echo "Порты 80/443 заняты не nginx и не Caddy. Освободите их или подключите панель к этой программе вручную."
    exit 1
else
    if ! command -v caddy >/dev/null 2>&1; then
        echo "==> Ставлю Caddy"
        apt-get update -qq
        apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -qq
        apt-get install -y -qq caddy
    fi
    mkdir -p /etc/caddy/conf.d
    grep -qs 'import /etc/caddy/conf.d/' /etc/caddy/Caddyfile || printf '\nimport /etc/caddy/conf.d/*.caddy\n' >> /etc/caddy/Caddyfile
    cat > "/etc/caddy/conf.d/$NAME.caddy" <<CADDY
$HOST {
    reverse_proxy 127.0.0.1:$PORT
}
CADDY
    if command -v ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then
        ufw allow 80/tcp >/dev/null
        ufw allow 443/tcp >/dev/null
    fi
    caddy validate --config /etc/caddy/Caddyfile >/dev/null
    systemctl enable --now caddy
    systemctl reload caddy
fi

echo
echo "Готово. Панель: https://$HOST"
echo "Вход: свой Telegram ID -> бот пришлёт код. Общий пароль (владелец): $(grep -E '^WEB_PASSWORD=' "$DIR/.env" | cut -d= -f2)"
echo "Первый запуск: сертификат выпускается до минуты, если страница не открылась, обновите её."
