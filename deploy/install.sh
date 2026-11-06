#!/usr/bin/env bash
# Установка, обновление или восстановление бота на сервере Ubuntu/Debian. Запускать от root:
#   bash /opt/<имя>/deploy/install.sh                        установить или обновить
#   bash /opt/<имя>/deploy/install.sh /root/backup_XXXX.zip  установить и восстановить всё из бэкапа
# Имя бота = имя папки: /opt/newlink -> сервис newlink, /opt/mango -> сервис mango. Можно ставить несколько ботов.
# Токен и ID владельца спросит один раз и запишет в <папка>/.env
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="$(basename "$DIR")"
cd "$DIR"

echo "==> Проверяю python3-venv"
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    apt-get update -qq && apt-get install -y -qq python3-venv
fi
id "$NAME" >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin "$NAME"

echo "==> Ставлю зависимости (1-3 минуты)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    read -rp "Токен бота от @BotFather: " TOKEN
    read -rp "Telegram ID владельца (несколько через запятую): " OWNERS
    printf 'BOT_TOKEN=%s\nOWNER_IDS=%s\nDATA_DIR=data\nLOG_LEVEL=INFO\n' "$TOKEN" "$OWNERS" > .env
fi

# Ключ шифрования токенов и веб-панель: добавляем, если ещё нет (в том числе при обновлении старой установки)
NEW_SECRET=""; NEW_PASS=""; PANEL_PORT=""
if ! grep -q '^SECRET_KEY=.' .env; then
    OLD_SECRET=""
    if [ $# -ge 1 ]; then  # восстановление из бэкапа: токены в нём зашифрованы старым ключом
        read -rp "SECRET_KEY из старой установки (Enter - новый, токены зеркал из бэкапа тогда не откроются): " OLD_SECRET
    fi
    NEW_SECRET="${OLD_SECRET:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')}"
    sed -i '/^SECRET_KEY=/d' .env
    printf 'SECRET_KEY=%s\n' "$NEW_SECRET" >> .env
fi
if ! grep -q '^WEB_PASSWORD=.' .env; then
    NEW_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(14))')"
    PANEL_PORT="$(python3 - <<'PY'
import socket
for port in range(8787, 8800):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port)); print(port); break
    except OSError:
        pass
    finally:
        s.close()
PY
)"
    sed -i '/^WEB_PORT=/d;/^WEB_PASSWORD=/d' .env
    printf 'WEB_PORT=%s\nWEB_PASSWORD=%s\n' "$PANEL_PORT" "$NEW_PASS" >> .env
fi
mkdir -p data

echo "==> Запускаю сервис $NAME"
systemctl stop "$NAME" 2>/dev/null || true
if [ $# -ge 1 ]; then
    .venv/bin/python -m bot.restore "$1"
fi

chown -R "$NAME:$NAME" data
chown "$NAME:$NAME" .env && chmod 600 .env
sed -e "s#/opt/newlink#$DIR#g" -e "s#^User=newlink#User=$NAME#" -e "s#^Group=newlink#Group=$NAME#" \
    -e "s#^Description=.*#Description=$NAME invite links bot#" deploy/newlink.service > "/etc/systemd/system/$NAME.service"
systemctl daemon-reload
systemctl enable --now "$NAME"
sleep 3
systemctl --no-pager --lines=5 status "$NAME" || true
echo
echo "Готово. Логи: journalctl -u $NAME -f"
if [ -n "$NEW_SECRET" ] && [ -z "${OLD_SECRET:-}" ]; then
    echo
    echo "ВАЖНО. SECRET_KEY сохранён в $DIR/.env. Скопируйте его и храните ОТДЕЛЬНО от бэкапов:"
    echo "без него токены зеркал и запасных ботов из бэкапа не открыть."
    echo "SECRET_KEY=$NEW_SECRET"
fi
if [ -n "$NEW_PASS" ]; then
    echo
    echo "Веб-панель (пароль владельца: $NEW_PASS, он же в .env) пока слушает только сервер."
    echo "Открыть её по обычной ссылке с HTTPS, с любого устройства:  bash $DIR/deploy/expose-panel.sh"
fi
