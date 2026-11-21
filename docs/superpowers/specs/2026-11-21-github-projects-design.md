# GitHub accounts, repository projects, and recorded demonstration

## Requested outcome

The user requested real GitHub account connections, repository access selection, use across projects, and a three-minute demonstration running against accessible GitHub repositories. They explicitly selected **one shared team workspace with multiple repositories**, not separate private user workspaces.

## Approach

Use a GitHub App device authorization flow for normal account connection: the administrator authorizes in GitHub, installs the app on selected repositories, and returns to CI Guard. This avoids a callback tunnel and keeps GitHub credentials out of browser storage. The deployment must supply its registered app's public client ID and slug. App registration/installation consent remains a GitHub action by the owner. Explicit administrator token connection and an opt-in server-provided token support self-hosted teams and the current environment's existing authenticated GitHub access. These are genuine GitHub API connections, never simulated accounts.

An OAuth App requesting broad classic repository scopes was rejected because a GitHub App provides selected-repository access. Public multi-tenant login was rejected by the user's workspace choice. These are implementation choices under the user's requested build.

## Security and permissions

- All GitHub/project administration requires the existing administrator bearer token, **including in local demo mode**. Connecting GitHub does not grant dashboard administrator rights.
- Personal/access tokens are encrypted at rest using a separate deployment key; environment credentials stay in environment configuration. No token is returned in connection/repository/status responses, logs, URLs, audit records, or the video.
- Device codes remain server-side and encrypted; short-lived user codes appear only during authorization. Enforce polling intervals and expiry. Expired account tokens require reconnecting; do not silently replace them with another credential.
- Repository access is checked against GitHub for connection and remote operations. GitHub App connections enumerate only installations and repositories authorized to that user and app. Revocation/403/expiry produces an explicit error.
- Separate local project records identify repository IDs, full names, default branches, and source connection. Disconnecting clears credentials and disables associated project tokens; run history remains.
- Project runner tokens are random, shown once, stored only as hashes, and restricted to a repository and explicitly selected dependencies. They cannot administer GitHub, inspect another project's runs, or claim another repository. The shared dependency retry pool remains global within this trusted team workspace.
- Request metadata/content/actions read to browse. Actions write is needed only to dispatch an existing workflow. Workflow creation, repository settings, and secret provisioning are not silently performed by the connection UI.

## HTTP contract

All paths below require a strict administrator bearer token.

- `GET /api/github/status`: `{device_flow_configured, environment_token_available, app_slug, install_url, connections: Connection[]}`.
- `POST /api/github/connect/token` with `{token}`; `POST /api/github/connect/environment`: verify identity and persist/attach a connection. Return safe `Connection`.
- `POST /api/github/device/start`: `{id,user_code,verification_uri,expires_in,interval}`. `POST /api/github/device/poll` with `{id}`: `{status: pending|slow_down|connected|expired|denied,connection?}`.
- `DELETE /api/github/connections/{id}`: disconnect locally. `GET /api/github/connections/{id}/repositories`: `Repository[]`.
- `GET /api/projects`, `POST /api/projects` with `{connection_id,repository_id}`, `DELETE /api/projects/{id}`: local selected projects.
- `GET /api/projects/{id}/workflows`: `Workflow[]`; `GET /api/projects/{id}/actions/runs`: safe recent GitHub workflow runs.
- `POST /api/projects/{id}/dispatch` with `{workflow_id,ref,inputs?}`: submit once; return accepted status and actual run link if provided by GitHub, otherwise repository Actions link. Never invent a run ID or retry a dispatch after an ambiguous transport failure.
- `POST /api/projects/{id}/runner-token` with `{dependency_ids: string[]}`: rotate one project token, return `{token,project_id,repository,dependency_ids}` once.

`Connection`: `{id,login,avatar_url,html_url,auth_type,created_at,expires_at?}`. `Repository`: `{id,full_name,name,private,default_branch,html_url,description?}`. `Project`: repository fields plus `{id,repository_id,connection_id,created_at}` (project ID is a string). `Workflow`: `{id,name,path,state,html_url}`. Remote run: `{id,name,status,conclusion,html_url,head_branch,event,created_at,updated_at,run_number}`. All GitHub paths derive from validated stored repository identity, not arbitrary client URLs. Fixed GitHub API origin and bounded pagination/timeouts.

## Interface

Add a Projects screen in the existing theme. Show real connection status, GitHub authorization, repository search/import, selected projects, recent GitHub checks and their links, workflow dispatch, and per-project installation instructions. Locked/unconfigured/empty/revoked states are explicit. Secrets are password inputs, cleared after use, and never stored in browser storage. Runner tokens require an explicit reveal/create action and disappear when their dialog closes. Workflow instructions preserve the required test check and use the project token. Do not show demo runs as actual GitHub activity.

## Live demonstration

Publish the reviewed application and self-contained demonstration workflow to the currently empty `ayushmanthehero-lab/toggle_wars` repository, as needed for the requested real GitHub demonstration. Do not modify other repositories' application code. The workflow provisions an isolated coordinator, self-hosted Flagsmith, and controllable dependency inside its runner. It also checks out `ayushmanthehero-lab/StreamPulse` at the inspected commit `5af46b946bdf453b8cf2e978d3310f51abc7e2f9` and wraps its existing Python unittest suite. Keep controlled outage evidence distinct from real consumer tests. No public coordinator tunnel or production secret is required.

Use actual GitHub run URLs and report artifacts in the recording. Account authorization secrets are excluded. Browser footage and readable captions form a three-minute walkthrough; waiting for GitHub queues can be cut with an explicit transition. The final artifact must be playable, have its duration verified, and show actual results. If remote permissions prevent an operation, preserve the exact error and never label local footage as a remote run.
