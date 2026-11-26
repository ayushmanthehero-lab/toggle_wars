#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --verify ) ]]; then
  echo "Usage: $0 [--verify]" >&2
  exit 2
fi
docker compose version >/dev/null
secret() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }
export EXPERIMENT_TEMP EXPERIMENT_POSTGRES_PASSWORD EXPERIMENT_DJANGO_SECRET
EXPERIMENT_TEMP="$(mktemp -d)"
EXPERIMENT_POSTGRES_PASSWORD="$(secret)"
EXPERIMENT_DJANGO_SECRET="$(secret)"
project="ci-guard-terminal-$(secret | cut -c1-10)"
compose=(docker compose -p "$project" -f infra/compose.experiment.yaml)
pids=()
cleanup() {
  if (( ${#pids[@]} )); then kill "${pids[@]}" 2>/dev/null || :; fi
  "${compose[@]}" down -v >/dev/null 2>&1 || :
  rm -rf -- "$EXPERIMENT_TEMP"
}
trap cleanup EXIT
python3 -m venv "$EXPERIMENT_TEMP/venv"
py="$EXPERIMENT_TEMP/venv/bin/python"
"$py" -m pip install -q -r requirements.txt
"${compose[@]}" up -d --wait --wait-timeout 300
"${compose[@]}" exec -T -u 0 flagsmith python manage.py shell   -c 'import sys; exec(compile(sys.stdin.read(), "terminal_bootstrap", "exec"))' <infra/bootstrap_flagsmith_terminal.py
export CI_GUARD_TERMINAL_CONFIG="$EXPERIMENT_TEMP/terminal.env"
# Prevent a caller's pre-existing runtime config from affecting this lab.
unset FLAGSMITH_ADMIN_API_URL FLAGSMITH_ADMIN_AUTH_SCHEME CI_GUARD_GITHUB_TOKEN
set -a
source "$CI_GUARD_TERMINAL_CONFIG"
CI_GUARD_ADMIN_TOKEN="$(secret)"
CI_GUARD_DEMO_MODE=true
CI_GUARD_DEMO_INVENTORY_URL=http://127.0.0.1:18190
set +a
unset ALL_PROXY all_proxy
export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}" no_proxy="127.0.0.1,localhost,${no_proxy:-}"
"$py" examples/inventory_service.py --port 18190 >"$EXPERIMENT_TEMP/inventory.log" 2>&1 &
pids+=("$!")
for pair in "18180:$TERMINAL_DEVELOPMENT_KEY" "18181:$TERMINAL_STAGING_KEY"; do
  port="${pair%%:*}"
  FLAGSMITH_ENVIRONMENT_KEY="${pair#*:}" CI_GUARD_DATABASE_PATH="$EXPERIMENT_TEMP/guard-$port.sqlite3"     "$py" scripts/serve.py --port "$port" >"$EXPERIMENT_TEMP/guard-$port.log" 2>&1 &
  pids+=("$!")
done
await_http() {
  for _ in {1..45}; do
    for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null || return 1; done
    curl -fs --noproxy '*' --max-time 2 "$1" >/dev/null && return 0
    sleep 1
  done
  return 1
}
await_http http://127.0.0.1:18190/health
await_http http://127.0.0.1:18180/api/ready
await_http http://127.0.0.1:18181/api/ready
if [[ "${1:-}" == --verify ]]; then
  "$py" scripts/flagsmith_terminal_demo.py "$CI_GUARD_TERMINAL_CONFIG"     "$TERMINAL_DEVELOPMENT_ID" "$TERMINAL_STAGING_ID" "$TERMINAL_PRODUCTION_ID"
else
  echo "Try: set $TERMINAL_STAGING_ID ci_guard_mode enforce"
  echo "The disposable Flagsmith stack shuts down when you quit."
  "$py" scripts/flagsmith_terminal.py --config "$CI_GUARD_TERMINAL_CONFIG" --write-env "$TERMINAL_STAGING_ID"
fi
