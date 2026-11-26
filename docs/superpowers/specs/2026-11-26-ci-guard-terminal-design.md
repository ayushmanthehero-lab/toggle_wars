# CI Guard terminal-first Flagsmith integration

## Goal
Make the terminal the primary interface to CI Guard and a real self-hosted
Flagsmith project. Discover the project's environments from its Management
API rather than requiring predefined names. Show default flag states and their
differences across environments, then change one named flag in one selected
environment and read it back. Keep the web dashboard for past runs and graphs
and keep the existing isolated experiment running unchanged.

## Approaches
A CI-only terminal would be simpler, but could not be reused to toggle other
flags in the same project. A universal Flagsmith administration console would
duplicate too much of the vendor dashboard. The proposed middle ground is a
project-scoped terminal for ordinary flag toggles, with dedicated CI Guard
controls for Observe/Enforce and the existing retry policies.

## Behavior and safeguards
- Authenticated terminal, configured against one trusted self-hosted origin
  and project. Discover names and IDs; never echo API keys or include secrets
  in examples.
- Browse and compare base states. Label them as defaults, not the effective
  behavior for all identities. Ordinary flags get a single on/off change.
  CI Guard's Observe/Enforce mode and numeric limits get validated inputs.
  Experiment configuration, multivariate traffic weights, identity/segment
  overrides, and protected flags are out of scope for writes.
- Writes require opting the environment in by ID, then confirming an exact
  environment/flag/before/after preview. Environments not opted in, including
  Production by default, are read-only. Change one environment at a time; no
  bulk writes or direct production changes during development.
- Bind each CI Guard coordinator to its own Flagsmith environment and
  independent retry database. Provide a repeatable two-environment local
  demo; leave existing installations and main branch untouched.

## Acceptance
From the terminal, discover two actual environments on the same self-hosted
project. Toggle a normal flag in one environment and verify Flagsmith reports
the sibling unchanged. Set a CI Guard policy to Enforce in one environment
and show real CI Guard outage decisions differ from the sibling's Observe
policy. Verify refusal for non-editable targets and a safe error on unavailable
Flagsmith. Existing dashboard, runner behavior, and experiment tests remain
covered. No paid service or public deployment is needed for the demo.
