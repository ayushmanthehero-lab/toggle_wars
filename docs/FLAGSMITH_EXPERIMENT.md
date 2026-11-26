# Observe vs Enforce: CI Guard's Flagsmith experiment

Question: when a shared dependency actually goes down, does a shared retry allowance reduce how many times the tests execute without hiding a code failure?

1. **Flag:** The disposable Flagsmith project's `ci_guard_mode` flag has an Observe control and a keyed Enforce variant. CI Guard already reads this flag by repository identity; the experiment checks what the coordinator actually applied.
2. **Metric:** Mean number of actual test executions per outage batch, lower is better. A blocked job that never starts contributes zero executions, not a pass. Separate healthy and assertion-failure checks must succeed in both arms.
3. **Traffic:** Flagsmith enrolls 100% of 144 synthetic repository identities, with 50% allocation per arm. Each identity runs an identical four-job workload against the same loopback failure fixture. Each batch has its own dependency ID, so its shared retry allowance is not contaminated by other subjects. The Python SDK writes a real exposure and measured outcome to Flagsmith's own Postgres database.
4. **Decision:** Ask Flagsmith to compute results. Require at least 50 subjects per arm, matching event counts and totals, a passing sample-ratio check, lower mean, and at least 95% win probability. Complete the experiment on the same flag only if the guardrails pass, then check the winning value for prior and unseen identities. This changes only the lab, not production. If the provider omits a balance statistic for its segment-based split, the runner separately tests the known 50/50 allocation against observed counts; it does not label that check a Flagsmith result.

## Measured result — November 26, 2026

| Flagsmith arm | Synthetic repositories | Executed tests | Mean per four-job outage batch |
| --- | ---: | ---: | ---: |
| Observe (control) | 74 | 888 | 12.00 |
| Enforce | 70 | 419 | 5.99 |

Flagsmith calculated a **50.12% reduction** in the mean and displayed a **100% chance to win for these controlled workloads**. Its version did not return a balance statistic for this split; the independent 50/50 check gave p = 0.739. Each arm passed one healthy check and preserved a genuine assertion failure, with both checks executing once. No outage job passed. Flagsmith completed the experiment and returned Enforce for the same flag for both prior arms and an unseen test identity.

[Redacted measured evidence](evidence/flagsmith-experiment-2026-11-26.json). This is a controlled simulation of traffic with real process execution, not production uplift or monetary savings. Native validation used Flagsmith source commit [181a4f6](https://github.com/Flagsmith/flagsmith/commit/181a4f6c335055bdca6b6b80e70692f36037a07f) and PostgreSQL 17.

## Reproduce without paid services

With Docker and Docker Compose, Python 3.12+, and curl:

```sh
./scripts/run_flagsmith_experiment.sh .local/flagsmith-experiment-results.json
```

The command starts a fresh, loopback-only Flagsmith/PostgreSQL stack and an authenticated CI Guard coordinator with a separate temporary SQLite file. It needs no Flagsmith Cloud credentials or analytics warehouse. It removes its own test containers and volumes on exit, while retaining the local evidence JSON. Do not run this pointed at a real environment or present its synthetic results as real-user impact. The scalar metric, cohort counts, inference and run statuses are kept distinct from existing Parallax and StreamPulse demo results.

Reference: [Flagsmith self-hosted Experimentation](https://docs.flagsmith.com/experimentation/self-hosting).
