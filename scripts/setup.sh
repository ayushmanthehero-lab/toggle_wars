#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
(cd web && npm ci && npm run build)
python3 scripts/prepare_env.py
docker compose --env-file .local/runtime.env up -d --wait postgres flagsmith
docker compose --env-file .local/runtime.env exec -T -u 0 flagsmith python manage.py shell -c 'exec(open("/bootstrap_flagsmith.py").read())'
echo 'Setup complete. Credentials remain in .local; see README.md for launch and authentication.'
