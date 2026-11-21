# GitHub integration verification — 21 November 2026

## Published source and actual runs

The complete reviewed project was published to the previously empty `ayushmanthehero-lab/toggle_wars` repository at commit [`227831a35c82082c22a06f91bda546da282715ad`](https://github.com/ayushmanthehero-lab/toggle_wars/commit/227831a35c82082c22a06f91bda546da282715ad).

- [CI Guard checks — successful](https://github.com/ayushmanthehero-lab/toggle_wars/actions/runs/44881383043).
- [Live repository demonstration — successful](https://github.com/ayushmanthehero-lab/toggle_wars/actions/runs/44881605727).

The live demonstration was dispatched **once from the application's Projects screen**, using the real connected GitHub account. The API returned accepted; GitHub subsequently reported the actual run as completed successfully. Acceptance was not presented as a passing test result.

## Downloaded artifact evidence

An independent check compared GitHub metadata, the published workflow, downloaded summary JSON, and individual reports. The run ID, workflow URL, source pins, attempt counts, and final outcomes matched.

| Evidence | Observed result |
|---|---|
| Controlled inventory outage, four jobs, observe | 12 actual test attempts |
| Same controlled outage, four jobs, enforce | 6 actual attempts, distributed 1 / 2 / 2 / 1 |
| Enforced job outcomes | All blocked with nonzero exits |
| Immediate outage rerun | Four blocked jobs, zero actual attempts |
| StreamPulse existing Python tests | 34 executed, 34 passed, zero skipped |
| StreamPulse guarded executions | Exactly one persisted CI Guard attempt |
| Consumer source pin | `5af46b946bdf453b8cf2e978d3310f51abc7e2f9` |
| Consumer environment | Python 3.13, NumPy 2.4.4, SciPy 1.17.1 |
| Consumer measured subprocess time | 5.486959 seconds |

The workflow runs the pinned StreamPulse tests **inside the dedicated toggle_wars GitHub job**. It does not change StreamPulse's source or install a new workflow in StreamPulse itself. The test suite starts its own localhost fixtures. No production outage or avoided retries are claimed for that suite. Its runner verification is explicitly `command_exit`; the additional demonstration verifier independently checks the actual unittest count and rejects empty or all-skipped success.

The retry reduction belongs to the deliberately broken inventory fixture. Four concurrent processes share the coordinator inside one GitHub job; this is not evidence of four separate GitHub-hosted runner machines. Public-repository compute reduction is not presented as a cash saving.

Artifacts are available from the linked GitHub run as `controlled-demo-evidence` and `streampulse-consumer-evidence`.

## Account and repository integration

- Actual GitHub identity verified as `ayushmanthehero-lab`.
- Actual selected repository projects: `toggle_wars` and `StreamPulse`.
- Real workflow metadata and recent GitHub checks displayed in the browser.
- A newly generated project-scoped credential executed a real pytest successfully. Claiming StreamPulse with the toggle_wars credential returned HTTP 403; GitHub administration was denied.
- Anonymous access to both the overview and GitHub administration returned HTTP 401 after connection.
- Stored account/device credentials are encrypted; project runner credentials are hashed, scoped, rotated, and revoked. The live demonstration's explicit server credential is not copied into source or the database.

The GitHub App device authorization implementation was verified with protocol tests and an independent concurrent-poll check. No app is registered for this deployment yet, so live device authorization was **not** performed. The user requested setup instructions; see [GITHUB.md](GITHUB.md). The actual account demonstration used the explicitly configured server credential.

## Local checks and review

**165 Python tests passed**, including 17 GitHub API/store tests, 25 GitHub HTTP client tests, and 12 project authorization tests. **16 frontend tests passed**; the production TypeScript/Vite build passed. One existing upstream Starlette/httpx deprecation warning remains.

Browser checks verified the live connected account/projects, project setup, repository filtering, real workflow selection and dispatch, and the strict unauthenticated lock. The Projects screen labels imported repositories as connected, not automatically protected.

The final Docker image built successfully with the new cryptography dependency. Its readiness endpoint passed, unauthenticated GitHub administration returned HTTP 401, and the administrator status confirmed encryption configuration without adopting an ambient GitHub credential. The [three-minute video](DEMO_VIDEO.md) passed duration, decode, playback, and sampled visual checks.

Independent security review found no release-blocking issue in the reviewed code. Concurrent device polls performed one outbound exchange; a completed-code replay returned HTTP 409. Device codes remained server-side, stored credentials were encrypted, refresh tokens were discarded, and dispatch never retried automatically.

The first direct GitHub HTTP check failed with `ConnectError [Errno 111] Connection refused` because the managed cloud requires its configured proxy. The client now honors operator proxy/CA configuration while retaining TLS verification, fixed GitHub origins, and no redirects. A real proxy regression and the live account request passed after correction.

## Remaining deployment steps

- Register/install a GitHub App before using its normal device sign-in flow.
- Provide a coordinator URL reachable from external runners, project-scoped repository secrets, and the actual project test command.
- Configure required checks in branch protection/rulesets. The current access returned `Resource not accessible by integration (HTTP 403)` for repository Actions settings; no settings or branch-protection changes were made.
- Continue using one coordinator replica with SQLite on local disk. This is one shared team workspace, not a multi-tenant hosting service.

This document extends the earlier [local verification](VERIFICATION.md); the earlier statement that no GitHub run had been performed was accurate before this extension.
