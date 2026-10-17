#!/usr/bin/env bash
# Установка и обновление бота на сервере Ubuntu/Debian. Запускать от root из папки с ботом:
#   bash deploy/install.sh                     установить или обновить
#   bash deploy/install.sh /root/backup.zip    установить и сразу восстановить всё из бэкапа
# Токен и ID владельца спросит один раз и запишет в .env
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME=postbot
cd "$DIR"

echo "==> Папка бота: $DIR"
# лучше держать бота в /opt/postbot. Если он лежит в /root или /home, сервис пойдёт от root,
# иначе у отдельного пользователя не будет доступа к папке
RUN_USER=$NAME
PROTECT_HOME=true
case "$DIR" in
    /root*|/home*) RUN_USER=root; PROTECT_HOME=false ;;
esac
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    echo "==> Ставлю python3-venv"
    apt-get update -qq && apt-get install -y -qq python3-venv
fi
if [ "$RUN_USER" = "$NAME" ]; then
    id "$NAME" >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin "$NAME"
fi

echo "==> Ставлю зависимости, это 1-3 минуты"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    read -rp "Токен бота от @BotFather: " TOKEN
    read -rp "Ваш Telegram ID (несколько через запятую): " OWNERS
    read -rp "Часовой пояс, например Asia/Almaty или +5 [Asia/Almaty]: " TZN
    printf 'BOT_TOKEN=%s\nOWNER_IDS=%s\nTZ_NAME=%s\nDATA_DIR=data\nLOG_LEVEL=INFO\n' \
        "$TOKEN" "$OWNERS" "${TZN:-Asia/Almaty}" > .env
fi
mkdir -p data

systemctl stop "$NAME" 2>/dev/null || true
if [ $# -ge 1 ]; then
    echo "==> Восстанавливаю из $1"
    .venv/bin/python -m postbot.restore "$1"
fi

chown -R "$RUN_USER:$RUN_USER" data
chown "$RUN_USER:$RUN_USER" .env && chmod 600 .env

cat > /etc/systemd/system/$NAME.service <<UNIT
[Unit]
Description=PostBot, конструктор постов
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m postbot
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=$PROTECT_HOME
PrivateTmp=true
ReadWritePaths=$DIR/data
MemoryMax=300M

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now "$NAME"
sleep 3
systemctl --no-pager --lines=5 status "$NAME" || true
echo
echo "Готово. Логи: journalctl -u $NAME -f"
