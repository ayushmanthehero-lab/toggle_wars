# CI Guard

**Keep shared-service outages out of pull-request tests. Verify staging separately before release.**

CI Guard separates an isolated pull-request test from a live release check. The inventory example boots a fresh dependency for each pull-request job, so it can still test code while staging is down. The live check keeps the existing shared retry allowance, self-hosted Flagsmith policy, and clear pass, fail, or blocked evidence. A React dashboard shows recorded runs and provides a controlled demonstration.

> Blocked tests do not pass. Require the secret-free isolated check for pull requests, and gate trusted releases on the separate guarded live smoke. Do not add `continue-on-error` or bypass either gate.

![CI Guard dashboard](docs/screenshots/overview.png)

## What is included

- Python command runner and reusable GitHub Action.
- FastAPI coordinator with transactional SQLite retry accounting and idempotent submissions.
- Live Flagsmith policy evaluation and repository targeting.
- Administrator-managed GitHub connections, selected repository projects, real workflow activity, and project-scoped runner credentials.
- Overview, run history, attempt details, dependency health, policy controls, and setup UI.
- Self-hosted Flagsmith/Postgres Compose deployment.
- A controllable inventory service and real pytest demo.
- Unit, concurrent execution, subprocess, and integration checks.

This is a single-organization self-hosted deployment. Run **one coordinator replica** with SQLite on local persistent disk. See [architecture](docs/superpowers/specs/2026-11-21-ci-guard-design.md) and [operations](docs/OPERATIONS.md) for boundaries.

## Start in the terminal (Docker only)

```sh
docker compose -f compose.quickstart.yaml run --rm --build terminal
```

Run this from the repo root in PowerShell, macOS Terminal, or Linux. The private
lab starts real self-hosted Flagsmith, compares Development, Staging, and
Production, and lets you change Staging safely. Try `outage` for actual CI test
runs and `setup-example` for the onboarding walkthrough. No separate Python,
Ubuntu installation, Flagsmith account, or paid host is needed.

[Quick start and bring-your-own-repo setup](docs/QUICKSTART.md) ·
[Terminal details and safety rules](docs/FLAGSMITH_TERMINAL.md)

## Start the full web dashboard

Requirements: Python 3.12+, Node.js 22+, npm, and Docker with Compose. Linux is the supported runner platform.

```bash
./scripts/setup.sh
```

Setup installs the locked dependencies, builds the web app, starts self-hosted Flagsmith, and creates the four policy flags. Credentials are generated once in ignored `.local/runtime.env` and `.local/flagsmith.env`; reruns preserve existing credentials and policy values.

Start the full application:

```bash
docker compose --env-file .local/runtime.env up -d --build guard
```

Open CI Guard at `http://localhost:8080` and Flagsmith at `http://localhost:8000`. The CI Guard Settings screen accepts the administrator token from `.local/runtime.env`. Enter it locally; do not paste it into chat or commit it. Flagsmith login email and password are also in that private file.

The default Compose ports bind only to loopback. To use external CI runners, deploy behind an HTTPS reverse proxy on a network reachable from those runners. Do not expose the unauthenticated demo service publicly.

## Flagsmith Experimentation

[Run the reproducible Observe vs Enforce experiment](docs/FLAGSMITH_EXPERIMENT.md) on an isolated copy of CI Guard. It uses real CI Guard runner executions and Flagsmith's calculated results, without a paid service or a production policy change.

## Controlled demo

Run the coordinator and inventory service with explicit demo mode:

```bash
CI_GUARD_DEMO_MODE=true docker compose --env-file .local/runtime.env --profile demo up -d --build guard inventory
```

Demo mode enables demonstration controls and unauthenticated local use. **It is for local demonstrations only.** When a Flagsmith key is configured, policy still comes from the real Flagsmith instance. The UI labels demo runs; it does not present them as GitHub-hosted executions.

1. In Policies select **Observe**, with two retries per job and a shared allowance of two.
2. In the Overview demo panel run **Dependency outage** with four jobs. Inspect actual attempts and would-block decisions.
3. Switch Policies to **Enforce**, then repeat the outage. The jobs share one allowance.
4. Restore the dependency using **Healthy**. If cooldown has not expired, read the report and use the dependency probe once eligible; rerun after recovery.
5. Run **Assertion failure** with the healthy dependency. The assertion remains a failure and is not retried as infrastructure trouble.
6. Run **Healthy** again. Actual test execution must pass before the run is green.

Observe is not proof of savings. CI Guard records execution seconds, actually prevented attempts, and would-prevent decisions separately. It does not invent dollar savings. Standard GitHub-hosted runners for public repositories are generally free; use an explicit applicable rate if estimating private-runner costs.

## Local development

After setup, start the example dependency and application in separate terminals:

```bash
.venv/bin/python examples/inventory_service.py
CI_GUARD_DEMO_MODE=true .venv/bin/python scripts/serve.py
```

For live UI development, start `npm run dev` in `web/`; Vite proxies `/api` to port 8080. `scripts/serve.py` serves `web/dist` after a production UI build. Local secrets are loaded without printing values.

Run all checks:

```bash
.venv/bin/python -m pytest
cd web
npm run build
npm test
```

For a running local demo with real Flagsmith, run `.venv/bin/python scripts/verify_demo.py`. Optional browser checks use `python scripts/browser_smoke.py --chromium /path/to/chromium` after installing Playwright in that Python environment.

The example intentionally failing scenarios are not part of the passing unit suite; the demo invokes `examples/test_inventory.py` explicitly.

## GitHub Action

Use the dashboard's Projects screen to connect GitHub and prepare repository-specific setup. [GitHub connection instructions](docs/GITHUB.md) cover GitHub App registration, selected repository access, token permissions, and external runner connectivity. One team workspace can connect multiple repositories; this is not a public multi-tenant login service.

There are two checks with different claims:

| Check | What green demonstrates | What it does not demonstrate |
|---|---|---|
| **Isolated inventory / pytest** in `CI Guard checks` | The repository's inventory test really ran and passed against a fresh loopback fixture. No deployed system, CI Guard credential, or Flagsmith is used. | That the independently deployed API, secrets, or live policy work. |
| **Guarded live inventory / pytest** in the reusable release workflow | The same test passed against a configured staging inventory endpoint through the CI Guard wrapper, with a recorded execution and the expected live Flagsmith policy. | Full API compatibility, production reliability, or that a different consumer/service tested successfully. |

Run the isolated integration locally: `python scripts/check_isolated_inventory.py --report .local/isolated-inventory.json` after installing `requirements.txt`. The check starts its own fixture; do not point it at a deployed endpoint. **This is an inventory reference implementation:** the live lane needs a staging endpoint with a deterministic `stock: 42` response or an explicitly adapted test. Do not point it at arbitrary production data. For the supported release caller, exact inputs and setup, see [GitHub checks and release smoke](docs/GITHUB.md#github-checks-and-release-smoke).

For other integrations, [the composite Action](action.yml) remains available; pin a reviewed commit and register the dependency first. Its command is split into an argument array, not evaluated as shell syntax. Use a checked-in script for commands that need shell operations. A wrapper exit of zero can mean process-exit success for non-pytest commands; the supplied release smoke requires structured pytest evidence as well as live policy.

## Flagsmith policy

| Flag | Default | Purpose |
|---|---:|---|
| `ci_guard_mode` | `observe` | `off`, `observe`, or `enforce`; disabled mode flag means off |
| `ci_guard_max_job_retries` | `2` | Maximum additional attempts for one job |
| `ci_guard_shared_dependency_retry_budget` | `2` | Shared additional attempts during a confirmed dependency outage |
| `ci_guard_dependency_cooldown_seconds` | `30` | Earliest eligible recovery probe |

The coordinator identifies repositories as `repo:owner/name` and supplies `repository` and `team` traits. Configure Flagsmith identity or segment overrides to roll out protection to selected repositories. The dashboard previews effective policy for a repository; its save action edits environment defaults. Repository overrides continue to take precedence.

Budget changes do not silently reset a current failure window. The active shared cap can tighten, and verified recovery resets the window. Changing observe/enforce mode preserves each mode's accounting. Off executes the test command once. If Flagsmith is unavailable or returns invalid policy, CI Guard records the error and executes once without retries; it does not claim protection was active.

## Reports and exits

| Exit | Meaning |
|---:|---|
| 0 | Direct pytest: actual test calls passed; other commands: successful process exit |
| 1 | Actual tests failed |
| 2 | Execution or further attempts blocked by policy |
| 3 | Configuration or coordination error |
| 124 | Test command timed out |

Reports include recorded attempts, bounded sanitized evidence, dependency health, policy fingerprint, decisions, and next action. GitHub receives a Markdown job summary and a downloadable JSON artifact. Coding assistants can use the JSON as evidence; CI Guard does not automatically fix code or control unrelated agent sessions.

Direct `pytest` and `python -m pytest` commands use a structured outcome adapter. An all-skipped, all-expected-failure, empty, or collection-only suite cannot report passed. Classification uses actual exception messages, so an error signature printed in test source does not trigger a dependency retry. Mixed assertion and dependency failures remain failures. Other commands report process-exit success explicitly; CI Guard cannot verify their individual test outcomes. Invoke pytest directly when its test-outcome guarantees are required.

## Documentation

- [Product scope](docs/project/PROJECT_VISION.md)
- [System map and release pipeline](docs/ARCHITECTURE.md)
- [Detailed API and UI design](docs/superpowers/specs/2026-11-21-ci-guard-design.md)
- [Implementation plan](docs/superpowers/plans/2026-11-21-ci-guard.md)
- [Operations and security](docs/OPERATIONS.md)
- [GitHub connections and repository projects](docs/GITHUB.md)
- [Verification evidence](docs/VERIFICATION.md)
- [Actual GitHub runs and integration evidence](docs/GITHUB_VERIFICATION.md)
- [Three-minute recorded walkthrough](docs/DEMO_VIDEO.md)

MIT licensed. Contributions should include a regression test for behavioral changes. Report security concerns privately before disclosing exploitable details.
