#!/usr/bin/env bash
# Обновить бота до последней версии из GitHub: bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
systemctl restart postbot
sleep 2
systemctl --no-pager --lines=5 status postbot || true
