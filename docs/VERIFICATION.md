# Verification evidence

Verified locally on 21 November 2026. This records observed behavior, not a claim that software is error-free or that an external GitHub deployment has been certified.

## Automated checks

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | **104 passed**: 55 coordinator/policy/health tests and 49 runner tests |
| `npm run build` in `web/` | TypeScript and production Vite build passed |
| `npm test` in `web/` | **8 passed** |
| `npm audit --audit-level=high` | **0 vulnerabilities** reported at verification time |
| `docker compose --env-file .local/runtime.env config --quiet` | Passed |
| Final Docker image build | Passed; optional trusted proxy CA supplied through a BuildKit secret |

The Python suite emitted one upstream `StarletteDeprecationWarning` about its TestClient's use of httpx. It did not fail a test. The npm audit result covers npm dependencies, not every component of the deployment.

The regression suite exercises concurrent reservations, idempotency, mode changes, shared budgets across differing repository policies, recovery leases/generations, authorization, policy failures, URL validation, command failures, timeouts, cancellation, and bounded secret redaction. Direct pytest execution also covers skipped/empty/collection-only suites, ordinary assertions, missing expected exceptions, mixed failures, actual dependency exceptions, and successful test calls.

## Actual Flagsmith and subprocess demonstration

`scripts/verify_demo.py` ran against the local coordinator, a running self-hosted Flagsmith instance, and the controllable inventory service. It changed actual Flagsmith values and restored the original policy afterward. These were actual pytest subprocesses, not fabricated counters.

Both modes used two per-job retries. Enforcement shared two retries across four jobs. The demonstration cooldown was one second.

| Scenario | Jobs | Actual attempts | Summed execution time | Result |
|---|---:|---:|---:|---|
| Healthy, observe | 1 | 1 | 0.678 s | Passed |
| Dependency outage, observe | 4 | 12 | 8.448 s | Failed |
| Recovered, observe | 1 | 1 | 0.569 s | Passed |
| Dependency outage, enforce | 4 | 6 | 4.052 s | Blocked, nonzero exits |
| Immediate outage rerun, enforce | 4 | 0 | 0 s | Still blocked, nonzero exits |
| Recovered, enforce | 1 | 1 | 0.570 s | Passed |
| Ordinary assertion failure, enforce | 2 | 2 | 1.139 s | Failed without dependency retries |
| Healthy final rerun, enforce | 2 | 2 | 1.267 s | Passed |

The outage comparison reduced actual attempts from 12 to 6 in this controlled run. Execution time is summed subprocess runtime, not elapsed wall time, runner billing, or a performance benchmark. Other checks ran on the same machine. Different scheduling can block initial attempts earlier; the enforce guarantee is a shared maximum of two extra retries during that failure window.

Generated run IDs and timestamps are retained in ignored `.local/verification-demo.json`. Reports and the dashboard use persisted attempt records. No cash savings are asserted.

## Container and package checks

The final image ran with self-hosted Flagsmith, Postgres, persistent coordinator storage, and the inventory container. `/api/ready` and the production dashboard returned HTTP 200. Effective policy came from `flagsmith` without a policy error. Two real pytest jobs passed with structured outcome counts.

The runner was also built and installed non-editably into a fresh virtual environment outside the checkout. From a separate temporary directory, it used a second repository identity and ran an actual dependency-checking pytest successfully. The installed pytest adapter verified one passed test call. This validates local packaging and reuse; it is not an independent developer installation in a second GitHub repository.

Flagsmith bootstrap was exercised against the pinned container. Repeating it preserved credentials and existing policy values. No credentials are included in this document or the dashboard bundle.

One temporary container verification command initially used the wrong report key and raised `KeyError: 'test_outcomes'`. The documented field is `pytest_outcomes`; correcting that assertion made the complete container check pass. No application behavior was changed to bypass it.

## Browser checks

Chromium exercised the running application, including the production build:

- Five screens at 1440, 768, and 390 pixel widths: 15 route/viewport checks without horizontal page overflow or JavaScript exceptions.
- Run search, empty search results, status filtering, detail dialog, JSON download, and Escape-to-close.
- Actual policy save/readback against Flagsmith, followed by restoration of the original policy.
- Repository policy preview, dependency registration, and an actual healthy probe.
- Reduced-motion rendering and mobile navigation.

The repeatable read-only portion is `scripts/browser_smoke.py`. [Desktop](screenshots/overview.png) and [mobile](screenshots/mobile.png) screenshots show real, explicitly labeled local demonstration records. Visual styling follows the supplied rounded white cards, pill navigation, pale background, dark selected controls, and blue/green/pink accents.

## Independent review and boundaries

Independent source review found and verified corrections for misleading pytest classification, skipped-suite success, startup cancellation cleanup, truncated/chunked secret exposure, recovery admission policy restrictions, and observe-mode accounting. The final reviewed server/runner/action source had no remaining release-blocking findings in that review. Review is not a guarantee against undiscovered defects.

Still external or explicitly outside this release:

- No GitHub push, published action release, remote workflow execution, or branch-protection change was performed. Configure the wrapper job as a required check and test it in the consuming repository.
- The four-job demonstration used concurrent local processes, not four separate GitHub-hosted machines. The included manual GitHub workflow reproduces those processes within one job.
- One coordinator replica and local SQLite disk are supported. Multi-tenant isolation, multiple coordinator replicas, SSO, automatic recovery scheduling, and a Postgres coordinator are not implemented.
- Direct pytest invocations verify actual test outcomes. Other commands explicitly report process-exit verification only.
- Cancelled partial-attempt runtime is available locally but is not included in coordinator aggregate execution time.
- Policy writes update four Flagsmith values separately. Interrupted writes can be partial and are reported as such.
- Deployment credentials, HTTPS routing, network reachability, backup restore, and private-runner pricing require validation for the target installation.
