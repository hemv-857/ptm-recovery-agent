#!/bin/sh
# Quick start: zero to measured results in one command. No keys needed.
set -e
cd "$(dirname "$0")/.."

echo "==> paytm Revenue Recovery Agent — quick start"

if [ ! -x .venv/bin/python ]; then
    echo "==> creating virtualenv"
    python3 -m venv .venv
fi
echo "==> installing dependencies"
.venv/bin/pip install -q -r requirements.txt

echo "==> running 2,000-case simulated batch (offline, ~30s)"
if ! .venv/bin/python scripts/run_batch.py; then
    echo "!! batch failed — check python3 --version (needs >= 3.10) and disk space"
    exit 1
fi

echo ""
echo "==> next steps:"
echo "    dashboard : .venv/bin/uvicorn app.main:app --port 8000  -> http://localhost:8000/"
echo "    api docs  : same server, browse /docs"
echo "    docker    : docker compose up --build"
