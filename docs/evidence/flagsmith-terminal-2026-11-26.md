# Terminal integration verification · 2026-11-26

This was an isolated run of the self-hosted Flagsmith 2.295.2 source at
`181a4f6c335055bdca6b6b80e70692f36037a07f`, PostgreSQL 17 and two CI
Guard coordinators with separate SQLite databases. No external CI jobs ran or
production environment changed.

The actual terminal commands were driven through stdin. The before/after
table was read from Flagsmith's Management API:

| Default | Development | Staging | Production (locked) |
| --- | --- | --- | --- |
| `ci_guard_mode` | observe → observe | observe → enforce | observe → observe |
| `show_retry_summary` | off → off | off → on | off → off |

The terminal refused the attempted Production edit even when its ID appeared
in the writable list. The controlled shared inventory outage then ran four
jobs in each configured environment:

| Environment | Policy from Flagsmith | Actual pytest executions |
| --- | --- | ---: |
| Development | Observe | 12 |
| Staging | Enforce | 6 |

No outage job was marked as passed. This is a two-environment demo, not the
independent randomized Flagsmith experiment or a customer savings estimate.

Replay: `./scripts/run_flagsmith_terminal_demo.sh --verify`.
