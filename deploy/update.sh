#!/usr/bin/env bash
# Обновление: bash deploy/update.sh. База и настройки сохраняются, миграции применятся сами.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
