#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[run.sh] Creating virtualenv..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f ".venv/.deps-installed" ] || [ requirements.txt -nt .venv/.deps-installed ]; then
    echo "[run.sh] Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
    touch .venv/.deps-installed
fi

if [ ! -f ".env" ]; then
    echo "[run.sh] ERROR: .env not found. Copy .env.example and fill it in."
    exit 1
fi

export $(grep -v '^#' .env | xargs -d '\n')

echo "[run.sh] Running alembic migrations..."
alembic upgrade head

echo "[run.sh] Starting uvicorn..."
exec uvicorn src.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8000}" --reload
