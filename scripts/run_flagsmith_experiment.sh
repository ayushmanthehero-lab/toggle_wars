#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
out="${1:-.local/flagsmith-experiment-results.json}"
if [[ -e "$out" ]]; then
  echo "Choose a new evidence output path; refusing to overwrite $out." >&2
  exit 1
fi
docker compose version >/dev/null
secret() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }
export EXPERIMENT_TEMP
EXPERIMENT_TEMP="$(mktemp -d)"
export EXPERIMENT_POSTGRES_PASSWORD EXPERIMENT_DJANGO_SECRET
EXPERIMENT_POSTGRES_PASSWORD="$(secret)"
EXPERIMENT_DJANGO_SECRET="$(secret)"
project="ci-guard-exp-$(secret | cut -c1-10)"
compose=(docker compose -p "$project" -f infra/compose.experiment.yaml)
started=0
fixture_pids=()
cleanup() {
  if (( ${#fixture_pids[@]} )); then kill "${fixture_pids[@]}" 2>/dev/null || :; fi
  if (( started )); then "${compose[@]}" down -v >/dev/null 2>&1 || :; fi
  rm -rf -- "$EXPERIMENT_TEMP"
}
trap cleanup EXIT
python3 -m venv "$EXPERIMENT_TEMP/venv"
py="$EXPERIMENT_TEMP/venv/bin/python"
"$py" -m pip install -r requirements.txt
started=1
"${compose[@]}" up -d --wait --wait-timeout 300
"${compose[@]}" exec -T -u 0 flagsmith python manage.py shell -c 'exec(open("/bootstrap_flagsmith_experiment.py").read())'
set -a
source "$EXPERIMENT_TEMP/experiment.env"
CI_GUARD_ADMIN_TOKEN="$(secret)"
CI_GUARD_DATABASE_PATH="$EXPERIMENT_TEMP/coordinator.sqlite3"
CI_GUARD_DEMO_MODE=false
CI_GUARD_EXPERIMENT_COORDINATOR_URL=http://127.0.0.1:18080
CI_GUARD_EXPERIMENT_HEALTHY_URL=http://127.0.0.1:18090
CI_GUARD_EXPERIMENT_OUTAGE_URL=http://127.0.0.1:18091
set +a
export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}" no_proxy="127.0.0.1,localhost,${no_proxy:-}"
unset ALL_PROXY all_proxy
"$py" examples/inventory_service.py --port 18090 >"$EXPERIMENT_TEMP/healthy.log" 2>&1 & fixture_pids+=("$!")
"$py" examples/inventory_service.py --port 18091 >"$EXPERIMENT_TEMP/outage.log" 2>&1 & fixture_pids+=("$!")
PYTHONPATH="$PWD/server:$PWD/runner" "$py" -m uvicorn ci_guard.app:app --host 127.0.0.1 --port 18080 >"$EXPERIMENT_TEMP/coordinator.log" 2>&1 & fixture_pids+=("$!")
await_http() {
  local url=$1
  for _ in {1..45}; do
    for pid in "${fixture_pids[@]}"; do kill -0 "$pid" 2>/dev/null || return 1; done
    curl -fs --noproxy '*' --max-time 2 "$url" >/dev/null && return 0
    sleep 1
  done
  return 1
}
await_http "$CI_GUARD_EXPERIMENT_COORDINATOR_URL/api/ready"
await_http "$CI_GUARD_EXPERIMENT_HEALTHY_URL/health"
await_http "$CI_GUARD_EXPERIMENT_OUTAGE_URL/health"
curl -fsS --noproxy '*' -H 'Content-Type: application/json' -d '{"healthy":false}' "$CI_GUARD_EXPERIMENT_OUTAGE_URL/__control" >/dev/null
"$py" scripts/flagsmith_experiment.py --output "$out"
