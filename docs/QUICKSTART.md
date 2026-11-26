# CI Guard Quick Start

## 1. Try it without an account

Install Docker Desktop on Windows/macOS or Docker Engine with the Compose plugin
on Linux. Start Docker and check `docker compose version`: use Compose v2.24+.
Docker Desktop should use Linux containers. On Windows you can run the commands
in PowerShell; you don't need to install Ubuntu or Python separately.

From the repository root:

```sh
docker compose -f compose.quickstart.yaml run --rm --build terminal
```

The first run downloads and builds images. It seeds a **real self-hosted Flagsmith**
and opens the terminal only after the services are ready. This is a disposable
private lab: it publishes no ports, uses no real credentials, and contacts
neither your Flagsmith project nor GitHub. It is **not** a production deployment.

1. Read the Development, Staging, and Production columns. The IDs are printed in
   the headings (`#2`, for example). The prompt prints the right Staging ID.
2. Type `set 2 ci_guard_mode enforce` (replace `2` as needed), then `apply 2` to
   confirm. The terminal reads the change back from Flagsmith. Try changing
   Production: it is read-only. Ordinary flags use `on` or `off`.
3. Type `outage`. Two independent CI Guard coordinators run four actual pytest
   jobs each against a controlled dependency outage. With Development still
   on Observe and Staging on Enforce, Staging should use fewer attempts. An
   outage must not turn into a passing test.
4. Type `setup-example`. The same setup checker used for a real repo verifies
   an example repo's scoped token and policy against the local coordinator and
   prepares an example workflow. The sample project record is local; it does
   not claim to contact GitHub or run a hosted workflow.
5. Type `quit`. This removes the terminal container. To stop and erase **only
   this quick-start lab** (including its local Flagsmith changes), run:

```sh
docker compose -f compose.quickstart.yaml down -v
```

If image pulls fail, check Docker's network access and registry availability.
If you see a port/startup conflict, quit the other quick-start session or
reset the lab with the command above. For scripted verification on a clean lab,
append `--verify` after `terminal`.

## 2. Use it with your own repository

Use a real non-production Flagsmith environment and a CI Guard coordinator
configured to use that environment's SDK key. A local Docker quick-start
coordinator is intentionally **not** reachable from GitHub-hosted runners.
See [Operations](OPERATIONS.md) for hosting, [GitHub setup](GITHUB.md) for
importing your repo and issuing a project token, and the
[terminal guide](FLAGSMITH_TERMINAL.md) for discovering environment IDs.

1. Register your staging dependency in CI Guard and issue a token scoped to that
   dependency and your repo. Create `ci/test.sh` in your repo. It should install
   your test dependencies as needed, run against isolated fixtures by default,
   and run a non-destructive staging smoke test when `CI_GUARD_LIVE=1`. Preserve
   the test command's exit code. Do not point this script at production.
2. In the **CI Guard checkout**, create the private untracked file
   `.local/onboard.env`. Replace the sample values locally. Do not paste these
   credentials into chat, show them in videos, or commit the file.

```dotenv
FLAGSMITH_ADMIN_API_URL=https://your-flagsmith.example/api/v1/
FLAGSMITH_PROJECT_ID=123
FLAGSMITH_ADMIN_API_KEY=your-restricted-management-key
# FLAGSMITH_ADMIN_AUTH_SCHEME=Api-Key
CI_GUARD_URL=https://your-staging-coordinator.example
CI_GUARD_PROJECT_TOKEN=cg_proj_your-scoped-runner-token
```

3. Use your real owner/repo, numeric environment ID, and registered dependency ID:

```sh
docker compose -f compose.quickstart.yaml run --rm --build onboard --repo example-org/my-app --environment 42 --dependency inventory
```

This checks the selected Flagsmith environment's **effective policy for that
repository**, whether the scoped token can see the dependency, and whether the
coordinator reports the same values. Matching values are not proof of exact
environment identity; verify the coordinator SDK key in its deployment
configuration. The checker uses no remote writes, never executes your tests,
and does not prove reachability from a GitHub-hosted runner. If your coordinator
is private, review the generated job's runner labels for your approved
self-hosted runner. PR jobs should stay isolated.

4. To generate a workflow, append `--emit-workflow`. Diagnostics go to stderr;
   stdout is workflow YAML only on success. For example, in PowerShell:

```powershell
$starter = docker compose -f compose.quickstart.yaml run --rm --build -T onboard --repo example-org/my-app --environment 42 --dependency inventory --emit-workflow
if ($LASTEXITCODE -eq 0 -and $starter) { $starter | Set-Content -Encoding ascii ci-guard-starter.yml }
```

For Linux/macOS, redirect to a **new review file**, not an existing workflow.
Review the output, then copy it into **your target repository** as
`.github/workflows/ci-guard.yml`. No repo is modified automatically.

5. In your target GitHub repo, create the `ci-guard-staging` environment.
   Allow only your trusted staging/release branch, protect that branch and
   require reviewers on the environment where supported. Add these
   **environment variables**: `CI_GUARD_URL`, `CI_GUARD_DEPENDENCY`, and
   `CI_GUARD_EXPECTED_MODE` (the mode printed by the checker). Add
   `CI_GUARD_PROJECT_TOKEN` as an **environment secret**. Do not put the
   Flagsmith Management API key in GitHub.
6. Let a PR run the isolated check. On the trusted protected ref, manually
   dispatch the separate staging check. It requires a first-attempt pass
   under healthy live Flagsmith policy; a fail-open run does not count.
   Verify both checks before using them as gates. The template does not
   alter repository rules, deploy, or run automatically on a release.

This can also run directly with a Python 3.12 environment and this repo's locked
dependencies: use `python scripts/ci_guard_onboard.py --help`. Export the
configuration as environment variables when using it without Compose.
