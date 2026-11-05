#!/usr/bin/env bash
# Установка на чистый сервер Ubuntu/Debian: bash deploy/install.sh
# Спросит токен и ID владельца, сам создаст пароль базы и ключ шифрования.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
    echo "==> Ставлю Docker"
    curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "Нужен Docker Compose v2 (команда docker compose). Обновите Docker." >&2
    exit 1
fi

if [ ! -f .env ]; then
    read -rp "Токен бота от @BotFather: " BOT_TOKEN
    read -rp "Ваш Telegram ID (несколько через запятую): " OWNER_IDS
    umask 077
    {
        printf 'BOT_TOKEN=%s\n' "$BOT_TOKEN"
        printf 'OWNER_IDS=%s\n' "$OWNER_IDS"
        printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
        printf 'SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
        printf 'LOG_LEVEL=INFO\n'
    } > .env
fi

echo "==> Собираю и запускаю (первый раз 1-3 минуты)"
docker compose up -d --build
sleep 3
docker compose ps
echo
echo "Готово. Логи: docker compose logs -f bot"
