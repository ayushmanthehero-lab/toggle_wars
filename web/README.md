# CI Guard dashboard

The dashboard displays coordinator records. It never seeds production metrics or invents saved compute time. The controlled demo is explicitly labeled and calls the coordinator's real demo endpoint.

## Development

From this directory:

```bash
npm ci
npm run dev
```

Vite serves port 5173 and proxies `/api` to the coordinator on port 8080. Start the coordinator using the root README first. Production builds are served by the coordinator from `web/dist`.

```bash
npm run typecheck
npm test
npm run build
```

## Screens

- **Overview:** recorded run totals, executed and prevented attempts, actual execution time, dependency health, and recent runs.
- **Runs:** search and repository/status filters, attempt evidence, immutable policy details, and JSON export.
- **Projects:** administrator-only GitHub account connections, accessible repository imports, real workflow history, explicitly confirmed remote dispatch, and scoped project runner tokens. GitHub App sign-in includes device authorization and selected-repository installation; unconfigured deployments show setup instructions.
- **Dependencies:** registration/editing, health probes, shared budget and recovery eligibility.
- **Policies:** current policy source, editable mode and limits, and repository-specific policy evaluation. Unsaved edits survive automatic data refresh.
- **Settings:** session access token, server configuration status, installable workflow example, and audit history.

Use an administrator token for configuration changes. Tokens stay in session storage (or page memory if browser storage is unavailable) and are sent only to the same-origin coordinator. Flagsmith credentials remain on the server.

## Interface behavior

The UI refreshes recorded activity every 15 seconds. A connection failure remains visible; old data retains its previous update timestamp. Dialogs support Escape, a keyboard focus trap, and focus restoration. Chart summaries include the actual data for screen readers. Fonts are bundled locally. Reduced-motion preferences disable decorative transitions.

`src/lib/format.test.ts` verifies missing-value handling, UTC timestamps, duration accuracy, truthful status colors, authorization headers, and server/proxy failure messages. Browser verification should additionally cover navigation, filtering, dialog keyboard behavior, responsive layout, policy editing, and dependency health checks against a running coordinator.

## GitHub data and secrets

Projects displays actual GitHub API records and is labeled separately from controlled local demos. The Projects routes require an administrator token even when local demo mode is enabled. GitHub access tokens are cleared from password inputs on submission and are never stored in browser storage. New project runner tokens remain only in their setup dialog and disappear when it closes. Dispatch requires a separate confirmation and makes one request; an ambiguous failure does not trigger a retry.

`src/lib/github.test.ts` verifies the GitHub HTTP contract, credential handling, and lack of automatic dispatch retries. `src/lib/demo.test.ts` verifies that demo totals come from actual returned reports and missing evidence is shown as unavailable, never as successful zero-cost execution.
