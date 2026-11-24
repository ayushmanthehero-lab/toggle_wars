# Connect GitHub and protect repository projects

CI Guard keeps one shared team workspace. A workspace administrator can connect GitHub accounts, import repositories they can access, inspect actual workflow runs, and issue a separate CI Guard runner credential for each project. GitHub sign-in does not create a second workspace or grant administrator access to this one.

## Unlock Projects

In Settings, enter the administrator credential from the private `.local/runtime.env` file. GitHub administration always requires this credential, even in demo mode. After the first real connection, anonymous demo access is disabled permanently to keep connected project records private. Keep the browser session authorized while recording or presenting the connected workspace.

Do not paste credentials into chat, commit them, or include them in a screen recording. The application clears account-token inputs after use and never returns saved GitHub credentials in API responses.

## GitHub App connection

1. [Register a GitHub App](https://github.com/settings/apps/new) under the account or organization that operates your CI Guard deployment. Use your project's public page as its homepage.
2. Enable **Device Flow**. This connection method needs no authorization callback, app private key, or client secret. Disable webhooks if you are not using them; CI Guard polls GitHub when the administrator refreshes a project.
3. Grant repository **Metadata: read** and **Actions: read** for browsing workflow activity. Use **Actions: read and write** only if administrators should dispatch existing workflows. CI Guard does not request GitHub Projects, repository settings, secrets, or workflow-file write permissions through this integration.
4. Install the app on **selected repositories**. Organization approval may be required. Keep repository access limited to the projects you intend to connect.
5. Configure the app's public client ID and slug on the CI Guard server:

   ```bash
   export CI_GUARD_GITHUB_CLIENT_ID='<public-client-id>'
   export CI_GUARD_GITHUB_APP_SLUG='<your-app-slug>'
   ```

   Restart the application after changing its environment. For Docker, export these values before `docker compose --env-file .local/runtime.env up -d guard`. The encryption key is generated once by `scripts/prepare_env.py` in the private runtime file.
6. In Projects, choose **Connect GitHub**, open GitHub's verification page, and enter the displayed short-lived code. Approve the account authorization on GitHub. CI Guard then verifies the identity and lists repositories accessible through the app's installations.

The public client ID differs from the numeric App ID. Device codes remain on the server; polling honors GitHub's interval and expiry responses. Expired account access requires reconnecting. This release does not silently refresh or switch account credentials.

## Fine-grained token or server-managed connection

Self-hosted administrators may instead connect a fine-grained GitHub token scoped to selected repositories. Supply it only through the Projects password input over a trusted HTTPS connection. It is encrypted before persistence using `CI_GUARD_GITHUB_ENCRYPTION_KEY`, which must remain outside the database.

An operator can explicitly provide `CI_GUARD_GITHUB_TOKEN` in the server environment. The Projects screen offers that configured connection without revealing the value. CI Guard never automatically adopts an ambient `GH_TOKEN` or `GITHUB_TOKEN`. Replacing or expiring a server-managed credential requires the operator to update the environment and reconnect.

Disconnecting removes the local account authorization and disables its project runner tokens. It does not uninstall the GitHub App or delete the original token at GitHub. Revoke that authorization in GitHub settings when you intend to remove issuer-side access too. Historical CI Guard runs remain audit evidence.

## Import repositories and install the wrapper

Import one or more repositories from the verified connection. A CI Guard project is a local repository configuration, not a GitHub Projects board. Repository identity is resolved from GitHub's immutable repository ID rather than accepting an arbitrary repository name from the browser.

For each project:

1. Register the actual dependency and its health URL in Dependencies.
2. Generate a project runner credential for the dependencies this repository may use. It is displayed once; creating a replacement invalidates the previous credential.
3. For the trusted release check, store that value as `CI_GUARD_PROJECT_TOKEN` where the release caller can pass it explicitly. Do not put it in a PR workflow, a command line, a report, or a repository file.
4. Configure the coordinator's reachable HTTPS URL, the registered dependency ID, and the staging inventory test URL in the release caller. GitHub-hosted runners cannot reach your workstation's localhost. Keep the staging endpoint dedicated to non-destructive test traffic, and keep administrator and Flagsmith credentials on the coordinator.
5. Use the split checks below. The composite Action is available separately for custom workflows; pin a reviewed action revision and configure the repository's actual test command and dependency. An imported repository is not automatically protected.

Project credentials cannot claim a different repository, report another project's runs, use dependencies outside their allowlist, or edit configuration. Retries still coordinate through the same shared dependency pool. Existing administrator and legacy workspace runner credentials retain their previous roles; use project credentials for new integrations.

## GitHub checks and release smoke

**Merge gate.** The `CI Guard checks` workflow runs **Isolated inventory / pytest** on pull requests, merge groups for merge queues, and pushes. It supports fork changes without CI Guard credentials; GitHub may still require approval before running a fork workflow. It starts a new loopback inventory fixture per job and runs `examples/test_inventory.py` with real pytest; no coordinator, Flagsmith connection, live inventory configuration, or CI Guard secret is needed. The check attempts to upload its sanitized `ci-guard-isolated-inventory-report.json` as `isolated-inventory-evidence`. Try the same check locally from the repository root:

```bash
python -m pip install -r requirements.txt
python scripts/check_isolated_inventory.py --report .local/isolated-inventory.json
```

Using a process in the job keeps this fixture simpler than a Docker service container. If you replace it with a [GitHub Actions service container](https://docs.github.com/en/actions/tutorials/use-containerized-services/use-docker-service-containers), use a Linux runner and a fresh job-scoped service. Container jobs reach it by service label; host jobs map a port to loopback. Service containers cannot be declared inside a composite Action.

If repository administrators want a mandatory merge gate, first confirm the exact check name in a real PR and, if applicable, a [merge queue check](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#merge_group). Then require that unique, isolated job in [branch protection or a ruleset](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches). This repository's configuration is not changed by these workflow files. Do not attach live secrets to untrusted PR code or use `pull_request_target` to run it.

**Release gate.** [`live-release.yml`](../.github/workflows/live-release.yml) is a [reusable workflow](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows), not a PR job or a deployment. Use a trusted **same-repository** release caller. The workflow verifies a `push` or `workflow_dispatch` on a protected branch or tag and fails rather than skipping on other refs/events. It checks out the caller’s exact commit. Restrict caller triggers to explicitly reviewed release branches or tags and ensure untrusted actors cannot create or update them; the GitHub protection flag alone does not prove the rules enforce that. Pass only the scoped project token by name; do not use `secrets: inherit`. Example jobs for a trusted **same-repository** caller:

```yaml
jobs:
  guarded-live-smoke:
    uses: ./.github/workflows/live-release.yml
    with:
      coordinator-url: ${{ vars.CI_GUARD_URL }}
      dependency: inventory-api
      inventory-url: ${{ vars.CI_GUARD_LIVE_INVENTORY_URL }}
      expected-mode: enforce
    secrets:
      CI_GUARD_PROJECT_TOKEN: ${{ secrets.CI_GUARD_PROJECT_TOKEN }}
  release:
    needs: guarded-live-smoke
    runs-on: ubuntu-latest
    steps:
      - run: echo "Run your existing reviewed release step here"
```

Configure named inputs and the secret first; use a project token scoped to this repository and the registered dependency. Use HTTPS endpoints without embedded credentials, query strings or fragments. Register the dependency with a health URL matching `<inventory-base>/health`. GitHub's [`needs` dependency](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds) keeps ordinary downstream release steps from running unless the smoke succeeds. Do not add a release `if: always()`, make the smoke optional, or mark the smoke as the required PR check: it is a different gate on a trusted path.

**Inventory reference only:** `scripts/check_live_inventory.py` uses the real runner and the same direct pytest test against the explicitly configured staging endpoint. Its read-only `GET /inventory` expects deterministic JSON with `stock: 42`; adapt the checked-in test explicitly for a different service or fixture. Redirects fail rather than testing another address. Do not use arbitrary production endpoints or data. Green requires one actual passing pytest invocation and an effective live Flagsmith policy with no recorded policy error and the expected mode (`enforce` by default; deliberately configured `observe` is also allowed; `off` is not). Missing or unusable configuration or evidence, a failed/blocked/timed-out test, or an errored, unavailable or unexpected policy stays non-green. The CLI may execute once with no retries on a policy lookup error; that fail-open runner behavior is **not** a healthy release result. A recovery probe before that single execution is allowed. If the runner needed to retry the test command, the release check stays non-green; rerun the smoke once staging has recovered. Inspect `ci-guard-live-inventory-report.json` in the sanitized `live-inventory-evidence` artifact when available. An artifact is evidence, not a replacement for the job result. A successful fixture test or smoke is not provider contract verification: [Pact provider verification](https://docs.pact.io/getting_started/verifying_pacts) replays the consumer's recorded requests against the provider and checks the expected responses. Add that separately if you need compatibility evidence.

## Workflow activity and dispatch

Projects displays actual GitHub workflow metadata and recent runs, with links back to GitHub. It does not equate every existing workflow with a guarded CI Guard execution. Install the wrapper before claiming protection for a workflow.

Dispatch requires an existing workflow declaring `workflow_dispatch`, a valid branch/ref, and Actions write permission. GitHub requires the workflow definition on the default branch. A connection that can read repository metadata may still lack permission to dispatch; the UI reports the actual permission error. Dispatch is submitted once. If the network response is ambiguous, check GitHub's run history before trying again.

## Demonstration used for this project

The dedicated workflow in `toggle_wars` provisions an isolated coordinator and Flagsmith inside its GitHub runner. No production coordinator credential or public tunnel is needed. It runs the controlled inventory outage scenarios, then checks out the pinned StreamPulse source and wraps its existing Python tests. StreamPulse's own repository files are not changed. Controlled fixture failures and real consumer tests are reported separately.

The [recorded walkthrough and verification evidence](GITHUB_VERIFICATION.md) identify the historical workflow run, source commit, test count, and artifact. That run used isolated demo services inside a GitHub job; it does not establish that the new staging release lane has run on GitHub. GitHub queues, installation approval, and permission grants are external to CI Guard and are never simulated as successful operations.
