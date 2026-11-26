# CI Guard Quick Start

Existing terminal setup needs host Python, shell utilities and Docker; real repo setup combines three documentation paths. A README-only change would improve discovery but keep these dependencies and manual checks. Use a Docker Compose command that launches a self-contained Flagsmith demo and a read-only setup command for real repos.

The demo uses pinned self-hosted Flagsmith, PostgreSQL, real CI Guard coordinators, and actual test commands. The lab publishes no host ports or GitHub writes. Development and Production remain read-only; only fixture Staging opts into edits. The same command works without host Python/Linux shell. Existing terminal shell runner, dashboard, experiment and main remain intact.

The onboarding check discovers the selected Flagsmith environment and compares the environment defaults without creating or updating the repository identity with the coordinator. A scoped runner token must allow the selected repo/dependency; errors are actionable and do not reveal secrets. Emit a pinned GitHub Actions starter only after a successful check. PRs run isolated without CI Guard credentials; manually dispatched staging smoke requires a protected ref and GitHub environment rules. Review the generated workflow; nothing is pushed, provisioned, or deployed by onboarding.
