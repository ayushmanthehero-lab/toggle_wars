# GitHub Projects Implementation Plan

**Goal:** Connect real GitHub accounts and selected repositories in the existing team workspace, then record a three-minute verified demonstration.

**Architecture:** Administrator-only GitHub integration, encrypted account credentials, local repository project records, project-scoped runner tokens, existing retry coordinator and Flagsmith policy. React adds Projects. GitHub Actions provides real demonstration evidence.

**Tech Stack:** Existing FastAPI/SQLite/React; httpx GitHub REST requests; cryptography for at-rest secrets; Playwright browser recording and ffmpeg video assembly.

**Spec:** `docs/superpowers/specs/2026-11-21-github-projects-design.md`.

## Constraints and review focus

- Keep one team workspace; no implied multi-tenant isolation or public signup.
- Authenticate GitHub administration even when demo mode is enabled.
- Prevent token leaks, repository impersonation, cross-project access, replayed/expired device authorization, and duplicate remote dispatch.
- Real GitHub data and recorded subprocess results only; report blocked external permissions honestly.
- Keep other repositories' code unchanged; use a pinned public consumer checkout in the dedicated demonstration workflow.

## Tasks

- [x] GitHub service: write failing authorization/credential/device/repository/dispatch tests; implement `server/ci_guard/github.py` and routes, encrypted persistence, bounded GitHub client; run new and existing backend tests.
- [x] Project authentication: write failing repository/dependency/run ownership and token rotation tests; extend existing application authentication with project-scoped tokens; verify shared retry accounting remains unchanged.
- [x] Dashboard: add `web/src/components/Projects.tsx`, API/types and navigation; implement real connection/repository/project/workflow controls and setup states; build and exercise meaningful flows.
- [x] Demonstration workflow: extend isolated demo to run pinned StreamPulse tests; verify locally, inspect final source for secrets, commit/push to empty owned repository, dispatch once, inspect actual run and artifacts.
- [x] Integration/review: exercise the actual environment credential connection, select available repositories, verify real GitHub workflow results, run full Python/UI checks and independent security review.
- [x] Video: record live browser interactions with captions and sanitized actual GitHub evidence; assemble a three-minute MP4, verify playback/duration and deliver links.

Evidence: `docs/GITHUB_VERIFICATION.md` and `docs/DEMO_VIDEO.md`. Live GitHub App device authorization remains a deployment setup step because no app is registered yet; the user requested instructions and the actual demonstration used explicit server-provided GitHub access.

## Ownership

GitHub service agent owns new GitHub service/routes/tests. Coordinator agent owns existing app integration and project runner authorization tests. Dashboard agent owns web changes. Runner agent owns dedicated GitHub demonstration workflow/script additions. Root owns requirements/config/docs, real GitHub integration, remote release operations, and video. Agents agree interfaces before editing shared files.
