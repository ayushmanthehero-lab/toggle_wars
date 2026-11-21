# CI Guard — project vision

Version: 1.0. Source: the user's detailed CI Guard brief of 2026-11-21, including the two attached dashboard references. The user authorized architecture, design, and end-to-end implementation. Technical choices below are implementation decisions, not quotations or additional user requirements.

## Product
CI Guard is a self-hosted GitHub Actions integration that prevents repeated test attempts against a confirmed failing dependency. Jobs share a retry allowance. Flagsmith controls enforcement and retry policy by repository. The dashboard explains decisions using recorded evidence.

## Deliverables
- A concurrency-safe coordinator with persistent run, attempt, dependency, policy, and audit records.
- A Python test-command wrapper and reusable GitHub Action, with JSON and GitHub Markdown reports.
- A responsive React/TypeScript dashboard inspired by the supplied images: soft gray background, rounded white cards, pill navigation, dark text, restrained blue/green/pink charts.
- A self-hosted Flagsmith deployment and reproducible initialization, with actual policy evaluation.
- A controllable dependency and concurrent demo exercising observe, enforce, assertion failure, and recovery.
- Tests, Docker setup, architecture and operational documentation, and CI for this repository.

## Non-negotiable behavior
A blocked or unexecuted test suite never passes. Unknown errors and assertion failures remain failures without retries. A retry requires configured dependency evidence; repeated expensive retries share an atomic allowance. Observe records would-block decisions without calling them saved attempts. Off executes the command once. Jobs finish promptly instead of waiting for recovery. Reports distinguish actual time from estimated avoided time and compute usage from cash savings. Secrets do not enter browser bundles, source control, or reports.

## Full release boundary
A complete, usable single-organization self-hosted release, not an enterprise SaaS. A single coordinator uses SQLite WAL and transactions on local persistent disk. Multiple job clients may run concurrently; multiple coordinator replicas and network-filesystem databases are unsupported. Authentication is a configured team token with explicit local demo mode. Public multi-tenant hosting, SSO, automatic workflow rescheduling, automatic code fixes, and arbitrary AI error classification are out of scope. These limits do not remove any of the requested workflow.

## Acceptance criteria
1. Four concurrent failing jobs cannot collectively receive more retries than the configured shared budget.
2. Assertion failure, unknown failure, timeout, blocked execution, and success produce distinct reports and truthful nonzero/zero exits.
3. Flagsmith mode/retry changes affect later policy evaluations without changing workflows.
4. A broken dependency, evidence, budget, and recovery instructions are visible in job summaries and the UI.
5. Recovery reopens execution only after an eligible health probe succeeds; probes are single-flight.
6. Every UI action works or explicitly describes unavailable configuration. No invented production metrics.
7. Docker startup, a real Flagsmith evaluation, local end-to-end demo, concurrency tests, UI build, and browser workflows are verified and reported separately.
8. Setup instructions explain required GitHub checks and never configure skipped jobs as the test gate.
