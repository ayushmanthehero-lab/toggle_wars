# CI Guard system map

```mermaid
flowchart LR
    Developer[Developer pushes a change] --> GitHub[GitHub Actions required test job]
    GitHub --> Runner[CI Guard Python wrapper]
    Runner -->|Register run / report actual attempt| API[Coordinator API]
    API -->|Evaluate repository identity| Flagsmith[Self-hosted Flagsmith]
    Flagsmith --> FlagsDB[(Flagsmith Postgres)]
    API -->|Atomic reservation / evidence / audit| Database[(SQLite WAL on local disk)]
    API -->|Bounded registered health probe| Dependency[Shared inventory API or test service]
    Runner -->|Only when authorized| Tests[Actual test command]
    Tests --> Dependency
    Runner --> Report[GitHub summary + JSON artifact]
    API --> Dashboard[React dashboard]
    Dashboard -->|Administrator controls| API
```

Flagsmith stores policy. The coordinator stores operational state. The runner executes the actual tests. The dashboard presents recorded evidence. None of these components turns a blocked test into a successful required check.

The detailed [architecture and workflow design](superpowers/specs/2026-11-21-ci-guard-design.md) defines policy semantics, state transitions, concurrency, HTTP contracts, UI design, authentication, and verification. [Operations](OPERATIONS.md) covers setup, credentials, recovery and backup. [Verification](VERIFICATION.md) records observed results and external checks not yet performed.

## Release pipeline

```mermaid
flowchart TD
    Commit[Change to CI Guard] --> Unit[Python coordinator + runner regression suites]
    Commit --> UI[TypeScript build + dashboard tests]
    Unit --> Review[Independent source review]
    UI --> Review
    Review --> Integration[Live Flagsmith + actual subprocess scenarios]
    Integration --> Browser[Browser interactions and responsive checks]
    Browser --> Docker[Build and exercise container deployment]
    Docker --> Docs[Setup and evidence review]
    Docs --> Publish[User-controlled GitHub push and release]
```

The supplied `CI Guard checks` workflow runs the Python and UI checks on pushes and pull requests. The manual `Controlled CI Guard demonstration` workflow creates a temporary Flagsmith deployment and runs four concurrent clients inside one job. It is a local concurrency demonstration, not evidence that four separate GitHub-hosted runner machines were tested.
