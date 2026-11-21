# Operating CI Guard

## Deployment and trust
CI Guard supports one organization, one coordinator replica, and SQLite on local disk. Multiple concurrent CI jobs are supported through serialized transactions. It is not a multi-tenant SaaS and does not support multiple coordinator replicas or an SQLite database on NFS. A Postgres coordinator migration is a separate future change.

CI_GUARD_TOKEN is the runner credential. CI_GUARD_ADMIN_TOKEN controls dependency and policy administration. The administrator token must not be installed in GitHub workflows. Both are generated privately. In production require HTTPS, restrict network access, and rotate tokens if exposed. Browser tokens stay in session storage until sign-out/clear; do not use the administrator dashboard on an untrusted device. Demo mode bypasses authentication and is only appropriate on loopback/private demo networks.

Flagsmith management credentials stay in the coordinator. The SDK environment key, management key and dashboard login are different credentials. FLAGSMITH_ENVIRONMENT_ID refers to the environment API key used by the management URL, not the numeric database row ID. A policy save updates four separate Flagsmith values; if interrupted, the UI reports an uncertain/partial write and requires a reload. There is no distributed transaction spanning those writes.

## Dependency configuration
Administrators register an explicit HTTP(S) health endpoint and literal error signatures. A health endpoint should represent the availability needed by the test suite. Do not use an endpoint that always returns 200 while the actual operation is broken. A successful health check is not proof that every test will pass.

The server rejects credentials in URLs, redirects, non-HTTP schemes and cloud metadata targets. Private service addresses are intentionally supported. Treat dependency editing as a privileged operation. Runner-supplied health evidence cannot independently reset an outage; the coordinator performs recovery probes.

## Failure and recovery
The first actual infrastructure failure plus unhealthy dependency evidence opens an outage window. Only configured matching infrastructure errors can receive retries. Assertion and unknown errors remain ordinary failures. Retries are reserved atomically before execution. A crashed runner may leave a reserved retry unused; CI Guard favors avoiding extra attempts over reclaiming uncertain work.

Observe and enforce counters remain distinct within an outage. Mode switches do not erase enforce spending. When targeted repository policies disagree, the active shared cap can tighten; the pool is not duplicated per repository. A cooldown makes a recovery probe eligible; it does not make the dependency healthy. One lease holder probes at a time. Stale lease completions cannot clear another outage generation.

When the coordinator is unreachable, the runner writes an actionable error report and does not invent shared-budget permission. When only Flagsmith is unavailable, the coordinator permits one actual command without retries and records the policy error. Blocking cannot convert missing tests into success. Recovery requires a new workflow run; no runner remains asleep awaiting recovery.

## Backups and retention
Stop the coordinator before copying its persistent database, or use SQLite's online backup API. Keep the backup, deployment configuration and secrets in secure storage. Back up Flagsmith Postgres separately with pg_dump. Never commit the database, logs or generated .local files. Current release preserves all run/decision records; plan disk capacity and archive the database periodically. There is no background destructive retention task.

Database schema version is checked on startup. Unsupported versions fail explicitly rather than dropping data. Restore first into an isolated environment and exercise policy evaluation, dependency registration, one actual passing test, and historical report access.

## Logs and reports
Reports are bounded and sanitize common credentials; full command output may still be visible in the CI job's normal logs. Do not put secret values directly in command arguments or error signatures. Use environment secrets. Public runner minutes avoided are compute reduction, not necessarily bill reduction. Control-plane request time and wrapper overhead are not counted as test execution time.

## Deployment checks
- GET /api/health confirms process liveness; test policy/source separately for Flagsmith readiness.
- Confirm authenticated /api/settings and /api/policy; neither should return secret values.
- Run a real registered test command; inspect recorded exit and duration.
- Check required GitHub check/ruleset configuration in the repository settings.
- Exercise backup restore, not only backup creation.

Self-hosted Flagsmith may emit upstream startup warnings. They do not prove or disprove the health of CI Guard. Review upstream upgrade/security guidance before exposing any management interface beyond a trusted network.

## Corporate proxy certificate support

Docker dependency layers accept an optional BuildKit secret named `proxy_ca`. Supply your trusted CA bundle without disabling TLS verification or embedding the CA in the image:

```bash
docker build --secret id=proxy_ca,src=/path/to/trusted-ca-bundle.pem -t ci-guard-guard .
docker compose --env-file .local/runtime.env up -d --no-build guard
```

Normal public-network builds need no secret. This was used in the managed cloud verification because its HTTPS proxy certificate is not automatically installed in nested containers.

## Interrupted attempt measurement

A cancelled in-flight command's partial duration is retained in the runner's local JSON/summary. The coordinator records cancellation but does not currently include that partial attempt in aggregate execution time. Interpret dashboard execution totals as recorded completed attempts.
