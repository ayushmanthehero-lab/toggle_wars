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
3. Store that value in the repository's GitHub Actions secrets as `CI_GUARD_PROJECT_TOKEN`.
4. Set `CI_GUARD_URL` as a repository variable pointing to the coordinator's reachable HTTPS URL. GitHub-hosted runners cannot reach this workspace's localhost. Self-hosted runners may use a suitable private network address.
5. Copy the generated workflow, select the reviewed CI Guard action commit, and configure the repository's actual test command and dependency ID.
6. Require the guarded test job in repository rulesets or branch protection. Do not add `continue-on-error` or skip the required gate.

Project credentials cannot claim a different repository, report another project's runs, use dependencies outside their allowlist, or edit configuration. Retries still coordinate through the same shared dependency pool. Existing administrator and legacy workspace runner credentials retain their previous roles; use project credentials for new integrations.

## Workflow activity and dispatch

Projects displays actual GitHub workflow metadata and recent runs, with links back to GitHub. It does not equate every existing workflow with a guarded CI Guard execution. Install the wrapper before claiming protection for a workflow.

Dispatch requires an existing workflow declaring `workflow_dispatch`, a valid branch/ref, and Actions write permission. GitHub requires the workflow definition on the default branch. A connection that can read repository metadata may still lack permission to dispatch; the UI reports the actual permission error. Dispatch is submitted once. If the network response is ambiguous, check GitHub's run history before trying again.

## Demonstration used for this project

The dedicated workflow in `toggle_wars` provisions an isolated coordinator and Flagsmith inside its GitHub runner. No production coordinator credential or public tunnel is needed. It runs the controlled inventory outage scenarios, then checks out the pinned StreamPulse source and wraps its existing Python tests. StreamPulse's own repository files are not changed. Controlled fixture failures and real consumer tests are reported separately.

The recorded walkthrough and verification evidence identify the actual workflow run, source commit, test count, and artifact. GitHub queues, installation approval, and permission grants are external to CI Guard and are never simulated as successful operations.
