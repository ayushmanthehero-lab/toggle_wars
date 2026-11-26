# CI Guard · Flagsmith terminal

Run CI Guard's first-party terminal against real self-hosted Flagsmith environments.
Compare a flag's default in each environment, preview a change, and read it back
after saving. CI Guard's existing web dashboard remains available for runs and graphs.

## Try it with disposable Flagsmith

For a fresh install, use the [Docker-only quick start](QUICKSTART.md). It works
from PowerShell without a separate Ubuntu installation. The original Bash
runner remains available on Linux or WSL with Docker, curl, and Python 3.12+:

```bash
./scripts/run_flagsmith_terminal_demo.sh
```

The command starts a fresh loopback-only Flagsmith/PostgreSQL lab and two CI
Guard coordinators. It does not access your own Flagsmith project or GitHub
resources, and shuts down the lab when you quit. Use the displayed numeric
Staging ID in the prompts:

```text
set <staging-id> show_retry_summary on
apply <staging-id>
set <staging-id> ci_guard_mode enforce
apply <staging-id>
refresh
quit
```

The fixture flag `show_retry_summary` demonstrates a regular on/off toggle;
it is not a CI Guard policy setting. `ci_guard_mode` is a real CI Guard
policy consumed by the coordinators. Prefer these restrictions:

- Development and Production are unchanged when you edit Staging.
- The table lists **environment defaults**; identity/segment overrides still apply.
- Show only matching flags with `refresh ci_guard`.

To replay and verify an outage across two coordinators with separate databases:

```bash
./scripts/run_flagsmith_terminal_demo.sh --verify
```

This drives the same terminal prompt through stdin, reads both edits back from
Flagsmith, checks Production refusal, then executes four actual pytest jobs
per environment through CI Guard. Observe and Enforce should produce different
attempt counts; neither should mark the outage as passed. This controlled
check is separate from the [Flagsmith experiment](FLAGSMITH_EXPERIMENT.md).
The companion GitHub Actions workflow runs the same verification.

## Connect your own project

Install this repo's `requirements.txt` in a Python 3.12+ virtual environment
(or run the repo's existing setup). Pass `--config <private-file>` with:

```dotenv
FLAGSMITH_ADMIN_API_URL=https://your-flagsmith-host.example/api/v1/
FLAGSMITH_PROJECT_ID=123
FLAGSMITH_ADMIN_API_KEY=<private-management-key>
# FLAGSMITH_ADMIN_AUTH_SCHEME=Api-Key
```

The default config path is `.local/flagsmith.env`; environment variables
override file contents. Don't commit or share the token; use an appropriately
scoped token where supported. HTTPS is required for non-loopback hosts.

```bash
python scripts/flagsmith_terminal.py list
python scripts/flagsmith_terminal.py dashboard --write-env 1234
```

Use the actual environment ID shown in the list. Every environment is
read-only unless opted in by ID. Production (including names containing
`prod` or `production` as words), environments requiring approval or
versioning, and protected, archived, server-key-only, and multivariate
flags cannot be edited from this terminal. Use Flagsmith's dashboard for
those workflows. Ordinary switches preserve flag values; CI Guard edits
validate the value and do not override targeting rules. A stale preview
requires a fresh review. If a write cannot be verified, refresh before
retrying; a failed verification is not a rollback.

To use real CI Guard retry policies across independent environments, run
a separate coordinator for each with its own `FLAGSMITH_ENVIRONMENT_KEY`,
admin credentials, configured endpoint, and `CI_GUARD_DATABASE_PATH`.
Point each workflow at the corresponding coordinator. Do not share a retry
database across environments. See [Operations](OPERATIONS.md) for secure
hosting.
