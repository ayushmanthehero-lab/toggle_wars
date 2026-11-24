import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Github,
  Plus,
  ArrowUpRight,
  RefreshCw,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  KeyRound,
  Copy,
  Check,
  Search,
  GitBranch,
  FolderGit2,
  Link2,
  Unplug,
  Trash2,
  Play,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Info,
} from "lucide-react";
import { Modal } from "./Modal";
import { api, ApiError } from "../lib/api";
import { githubApi } from "../lib/github";
import type {
  Connection,
  GitHubStatus,
  Repository,
  Project,
  Workflow,
  RemoteRun,
  DeviceAuthorization,
  ProjectRunnerToken,
} from "../lib/github";
import type { Dependency } from "../lib/types";
import { formatDate, relativeTime } from "../lib/format";
import { liveDiagnosticWorkflow, splitCiDocs } from "../lib/liveDiagnostic";

type Props = {
  revision: number;
  notify: (message: string) => void;
  onSettings: () => void;
};
function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}
function ErrorNotice({ error }: { error: string }) {
  return (
    <div className="error-box" role="alert">
      <AlertTriangle size={18} />
      <div>
        <strong>The request was not completed.</strong>
        <p>{error}</p>
      </div>
    </div>
  );
}
function Loading({
  children = "Loading GitHub data…",
}: {
  children?: ReactNode;
}) {
  return (
    <div className="loading-state" role="status">
      <LoaderCircle size={21} className="spin" />
      <span>{children}</span>
    </div>
  );
}
function Empty({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <FolderGit2 size={27} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
function safeGitHubUrl(value: string | null | undefined) {
  try {
    const u = new URL(value || "");
    return u.protocol === "https:" && u.hostname === "github.com"
      ? u.href
      : undefined;
  } catch {
    return undefined;
  }
}
function GitHubLink({
  url,
  children,
  className = "text-button",
}: {
  url?: string | null;
  children: ReactNode;
  className?: string;
}) {
  const href = safeGitHubUrl(url);
  return href ? (
    <a className={className} href={href} target="_blank" rel="noreferrer">
      {children}
      <ArrowUpRight size={14} />
    </a>
  ) : (
    <span className="muted">GitHub link unavailable</span>
  );
}
function currentProject() {
  try {
    return sessionStorage.getItem("ci-guard-project") || "";
  } catch {
    return "";
  }
}
function rememberProject(id: string) {
  try {
    sessionStorage.setItem("ci-guard-project", id);
  } catch {
    /* View preference only. */
  }
}

export default function Projects({ revision, notify, onSettings }: Props) {
  const [status, setStatus] = useState<GitHubStatus | null>(null),
    [projects, setProjects] = useState<Project[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [locked, setLocked] = useState(false);
  const [localRevision, setLocalRevision] = useState(0),
    [connectOpen, setConnectOpen] = useState(false),
    [importOpen, setImportOpen] = useState(false),
    [selected, setSelected] = useState(currentProject),
    [pendingDelete, setPendingDelete] = useState<Project | null>(null),
    [pendingDisconnect, setPendingDisconnect] = useState<Connection | null>(
      null,
    ),
    [busy, setBusy] = useState(false),
    [mutationError, setMutationError] = useState("");
  const refresh = useCallback(() => setLocalRevision((n) => n + 1), []);
  const closeConnect = useCallback(() => setConnectOpen(false), []),
    closeImport = useCallback(() => setImportOpen(false), []),
    closeDelete = useCallback(() => {
      setPendingDelete(null);
      setMutationError("");
    }, []),
    closeDisconnect = useCallback(() => {
      setPendingDisconnect(null);
      setMutationError("");
    }, []);
  useEffect(() => {
    let active = true;
    Promise.all([githubApi.status(), githubApi.projects()])
      .then(([s, p]) => {
        if (!active) return;
        setStatus(s);
        setProjects(p);
        setError("");
        setLocked(false);
        setSelected((id) =>
          p.some((project) => project.id === id) ? id : p[0]?.id || "",
        );
      })
      .catch((e) => {
        if (!active) return;
        setError(message(e));
        setLocked(
          e instanceof ApiError && (e.status === 401 || e.status === 403),
        );
        setStatus(null);
        setProjects([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision, localRevision]);
  const project = projects.find((p) => p.id === selected) || null;
  function select(id: string) {
    setSelected(id);
    rememberProject(id);
  }
  async function removeProject() {
    if (!pendingDelete || busy) return;
    setBusy(true);
    setMutationError("");
    try {
      await githubApi.deleteProject(pendingDelete.id);
      notify("Project removed. Its runner token can no longer be used.");
      closeDelete();
      refresh();
    } catch (e) {
      setMutationError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function disconnect() {
    if (!pendingDisconnect || busy) return;
    setBusy(true);
    setMutationError("");
    try {
      await githubApi.disconnect(pendingDisconnect.id);
      notify("GitHub account disconnected from this workspace.");
      closeDisconnect();
      refresh();
    } catch (e) {
      setMutationError(message(e));
    } finally {
      setBusy(false);
    }
  }
  if (loading) return <Loading />;
  if (locked)
    return (
      <section className="card project-unlock">
        <span className="unlock-icon">
          <LockKeyhole size={28} />
        </span>
        <p className="eyebrow">ADMINISTRATOR ACCESS</p>
        <h2>Unlock your GitHub workspace.</h2>
        <p>
          GitHub connections and repository settings require an administrator
          token, even in the demo workspace. Connecting a GitHub account does
          not grant administrator access.
        </p>
        <button className="button dark" onClick={onSettings}>
          <KeyRound size={16} />
          Open connection settings
        </button>
        <details className="technical-detail">
          <summary>Connection response</summary>
          <p>{error}</p>
        </details>
      </section>
    );
  return (
    <div className="page-content projects-page">
      {error && <ErrorNotice error={error} />}
      <section className="card github-connection-card">
        <div className="github-connection-title">
          <span className="github-round">
            <Github size={26} />
          </span>
          <div>
            <p className="eyebrow">ONE SHARED TEAM WORKSPACE</p>
            <h2>Your GitHub connections</h2>
            <p>Choose real repositories. Keep access under your control.</p>
          </div>
          <div className="connection-card-actions">
            <button
              className="icon-button"
              onClick={refresh}
              aria-label="Refresh GitHub connections"
            >
              <RefreshCw size={17} />
            </button>
            <button
              className="button dark"
              onClick={() => setConnectOpen(true)}
              disabled={!status}
            >
              <Plus size={16} />
              Connect GitHub
            </button>
          </div>
        </div>
        {status?.connections.length ? (
          <div className="connected-accounts">
            {status.connections.map((connection) => (
              <div key={connection.id} className="connected-account">
                <span className="account-symbol">
                  <Github size={20} />
                </span>
                <span className="account-name">
                  <strong>{connection.login}</strong>
                  <small>
                    {connection.auth_type === "environment"
                      ? "Server-provided access"
                      : connection.auth_type === "device"
                        ? "GitHub App authorization"
                        : connection.auth_type.replaceAll("_", " ")}
                  </small>
                </span>
                <GitHubLink url={connection.html_url}>Profile</GitHubLink>
                <button
                  className="icon-button small"
                  aria-label={`Disconnect ${connection.login}`}
                  onClick={() => setPendingDisconnect(connection)}
                >
                  <Unplug size={15} />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="connection-empty">
            <span>
              <CircleDashed size={17} />
              No account connected yet
            </span>
            <p>
              Your GitHub credentials stay on the server. The dashboard never
              stores them.
            </p>
          </div>
        )}
        {status?.install_url && (
          <div className="github-install-note">
            <Info size={15} />
            <span>
              Need another repository? Update the GitHub App’s repository
              access.
            </span>
            <GitHubLink url={status.install_url}>
              Choose repositories
            </GitHubLink>
          </div>
        )}
      </section>
      <div className="section-toolbar">
        <div>
          <h2>
            Connected projects{" "}
            <span className="count-pill">{projects.length}</span>
          </h2>
          <p className="section-description muted">
            One project per repository. A shared budget for common dependencies.
          </p>
        </div>
        <button
          className="button"
          onClick={() => setImportOpen(true)}
          disabled={!status?.connections.length}
        >
          <Plus size={16} />
          Import repository
        </button>
      </div>
      {projects.length ? (
        <div className="project-grid">
          {projects.map((p) => (
            <section
              key={p.id}
              className={`card project-card ${p.id === selected ? "current" : ""}`}
            >
              <div className="project-card-heading">
                <span className="project-repo-icon">
                  <FolderGit2 size={24} />
                </span>
                <span
                  className={`project-visibility ${p.private ? "private" : ""}`}
                >
                  {p.private ? <LockKeyhole size={11} /> : <Github size={11} />}{" "}
                  {p.private ? "Private" : "Public"}
                </span>
                <button
                  className="icon-button small project-remove"
                  onClick={() => setPendingDelete(p)}
                  aria-label={`Remove project ${p.full_name}`}
                >
                  <Trash2 size={15} />
                </button>
              </div>
              <p className="project-owner">{p.full_name.split("/")[0]}</p>
              <h3>{p.name}</h3>
              <p className="project-description">
                {p.description || "Connected to your CI Guard workspace."}
              </p>
              <div className="project-meta">
                <span>
                  <GitBranch size={13} />
                  {p.default_branch}
                </span>
                <span>
                  <Link2 size={13} />
                  {status?.connections.find((c) => c.id === p.connection_id)
                    ?.login || "Account disconnected"}
                </span>
              </div>
              <div className="project-card-footer">
                <button
                  className={`button small ${p.id === selected ? "dark" : ""}`}
                  onClick={() => select(p.id)}
                >
                  <Check size={14} />
                  {p.id === selected ? "Selected project" : "Open project"}
                </button>
                <GitHubLink url={p.html_url}>GitHub</GitHubLink>
              </div>
            </section>
          ))}
        </div>
      ) : (
        <section className="card">
          <Empty
            title="Bring your first repository."
            description="Import a repository your connected account can access. CI Guard will show its real workflows and GitHub runs."
          >
            <button
              className="button dark"
              disabled={!status?.connections.length}
              onClick={() => setImportOpen(true)}
            >
              <Plus size={16} />
              Import a repository
            </button>
          </Empty>
        </section>
      )}
      {project && (
        <ProjectDetail
          key={project.id}
          project={project}
          revision={localRevision}
          notify={notify}
        />
      )}
      <div className="info-panel">
        <ShieldCheck size={22} />
        <div>
          <h3>Real GitHub activity, separate from the local demo.</h3>
          <p>
            Connecting a repository does not change its workflows or secrets.
            Set up the Action explicitly, and require its test check before
            merging.
          </p>
        </div>
      </div>
      {connectOpen && status && (
        <ConnectDialog
          status={status}
          onClose={closeConnect}
          onConnected={() => {
            refresh();
            closeConnect();
            notify(
              "GitHub account connected. You can now import accessible repositories.",
            );
          }}
        />
      )}
      {importOpen && status && (
        <ImportDialog
          connections={status.connections}
          projects={projects}
          onClose={closeImport}
          onImported={(p) => {
            select(p.id);
            refresh();
            closeImport();
            notify(`${p.full_name} imported into this workspace.`);
          }}
        />
      )}
      {pendingDelete && (
        <Modal
          title="Remove this project?"
          description={pendingDelete.full_name}
          onClose={closeDelete}
        >
          <p className="confirmation-copy">
            This removes the local project and disables its runner token. The
            GitHub repository is not deleted. Recorded test history remains.
          </p>
          {mutationError && <ErrorNotice error={mutationError} />}
          <div className="modal-actions">
            <button className="button" disabled={busy} onClick={closeDelete}>
              Keep project
            </button>
            <button
              className="button danger-button"
              disabled={busy}
              onClick={removeProject}
            >
              {busy ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Trash2 size={16} />
              )}
              Remove project
            </button>
          </div>
        </Modal>
      )}
      {pendingDisconnect && (
        <Modal
          title="Disconnect this GitHub account?"
          description={pendingDisconnect.login}
          onClose={closeDisconnect}
        >
          <p className="confirmation-copy">
            CI Guard will remove this stored connection and disable its
            associated project tokens. This does not uninstall your GitHub App
            or delete a repository.
          </p>
          {mutationError && <ErrorNotice error={mutationError} />}
          <div className="modal-actions">
            <button
              className="button"
              disabled={busy}
              onClick={closeDisconnect}
            >
              Keep connection
            </button>
            <button
              className="button danger-button"
              disabled={busy}
              onClick={disconnect}
            >
              {busy ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Unplug size={16} />
              )}
              Disconnect account
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function ConnectDialog({
  status,
  onClose,
  onConnected,
}: {
  status: GitHubStatus;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [authorization, setAuthorization] =
      useState<DeviceAuthorization | null>(null),
    [authStatus, setAuthStatus] = useState(""),
    [token, setToken] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [expiresAt, setExpiresAt] = useState(0),
    [copied, setCopied] = useState(false);
  const connected = useRef(onConnected);
  connected.current = onConnected;
  useEffect(() => {
    if (!authorization) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let interval = authorization.interval;
    const poll = async () => {
      if (!active) return;
      if (Date.now() >= expiresAt) {
        setAuthStatus("expired");
        return;
      }
      try {
        const result = await githubApi.pollDevice(authorization.id);
        if (!active) return;
        setAuthStatus(result.status);
        if (result.status === "connected") {
          connected.current();
          return;
        }
        if (result.status === "expired" || result.status === "denied") return;
        if (result.interval) interval = result.interval;
        else if (result.status === "slow_down") interval += 5;
        timer = setTimeout(poll, Math.max(interval, 1) * 1000);
      } catch (e) {
        if (active) setError(message(e));
      }
    };
    timer = setTimeout(poll, Math.max(interval, 1) * 1000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [authorization, expiresAt]);
  async function start() {
    setBusy(true);
    setError("");
    try {
      const data = await githubApi.startDevice();
      setAuthorization(data);
      setExpiresAt(Date.now() + data.expires_in * 1000);
      setAuthStatus("pending");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function environment() {
    setBusy(true);
    setError("");
    try {
      await githubApi.connectEnvironment();
      onConnected();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function connectToken(e: FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError("");
    const submitted = token.trim();
    setToken("");
    try {
      await githubApi.connectToken(submitted);
      onConnected();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function copyCode() {
    if (!authorization) return;
    try {
      await navigator.clipboard.writeText(authorization.user_code);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  const expired = authStatus === "expired" || authStatus === "denied";
  return (
    <Modal
      title="Connect GitHub"
      description="Authorize repository access for this shared team workspace."
      onClose={onClose}
    >
      <div className="form-stack">
        {error && <ErrorNotice error={error} />}{" "}
        {authorization ? (
          <div className="device-authorization">
            <span className="github-round">
              <Github size={30} />
            </span>
            <h3>
              {expired
                ? "Authorization needs to restart"
                : "Enter this code on GitHub"}
            </h3>
            <p>
              {expired
                ? `GitHub authorization ${authStatus}. Start again when you are ready.`
                : "GitHub will ask you to approve access. This page checks for approval at the required interval."}
            </p>
            <div className="device-code">
              <code>{authorization.user_code}</code>
              <button
                className="icon-button"
                type="button"
                onClick={copyCode}
                aria-label="Copy GitHub authorization code"
              >
                {copied ? <Check size={17} /> : <Copy size={17} />}
              </button>
            </div>
            {!expired && (
              <GitHubLink
                className="button dark"
                url={authorization.verification_uri}
              >
                Open GitHub authorization
              </GitHubLink>
            )}
            <span className="device-poll-state">
              {!expired && <LoaderCircle size={14} className="spin" />}
              {authStatus === "slow_down"
                ? "GitHub requested slower checks. Waiting…"
                : expired
                  ? "No connection was created."
                  : "Waiting for your approval on GitHub…"}
            </span>
            {expired && (
              <button className="button" onClick={start} disabled={busy}>
                Start again
              </button>
            )}
          </div>
        ) : (
          <>
            {status.environment_token_available && (
              <div className="server-connection-option">
                <div>
                  <span className="icon-disc">
                    <KeyRound size={18} />
                  </span>
                  <span>
                    <strong>Server-provided GitHub access</strong>
                    <small>
                      Use the GitHub credential explicitly configured for this
                      deployment.
                    </small>
                  </span>
                </div>
                <button
                  className="button dark"
                  onClick={environment}
                  disabled={busy}
                >
                  {busy ? (
                    <LoaderCircle className="spin" size={15} />
                  ) : (
                    <Link2 size={15} />
                  )}
                  Connect server account
                </button>
              </div>
            )}
            <div className="connect-method">
              <span className="connect-method-icon">
                <Github size={24} />
              </span>
              <div>
                <h3>Sign in with GitHub</h3>
                <p>
                  Authorize the GitHub App, then choose which repositories it
                  can access.
                </p>
              </div>
            </div>
            {status.device_flow_configured ? (
              <button className="button dark" onClick={start} disabled={busy}>
                {busy ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <Github size={17} />
                )}
                Continue with GitHub
              </button>
            ) : (
              <div className="quiet-note">
                <Info size={16} />
                <span>
                  GitHub device sign-in is not configured on this server. Follow
                  the App setup instructions below, or use a configured server
                  connection.
                </span>
              </div>
            )}
            {!status.device_flow_configured && (
              <details className="github-app-instructions">
                <summary>Configure GitHub App sign-in</summary>
                <ol>
                  <li>
                    Create a GitHub App in your GitHub developer settings.
                  </li>
                  <li>Enable the device authorization flow.</li>
                  <li>
                    Request repository Metadata read and Actions read access.
                    Request Actions write only if users will dispatch workflows.
                  </li>
                  <li>
                    Set the server’s <code>CI_GUARD_GITHUB_CLIENT_ID</code> and{" "}
                    <code>CI_GUARD_GITHUB_APP_SLUG</code>. Use the App’s public
                    client ID, not its numeric App ID.
                  </li>
                  <li>
                    Install the App on selected repositories, then restart the
                    coordinator with the configuration.
                  </li>
                </ol>
                <GitHubLink url="https://github.com/settings/apps/new">
                  Create a GitHub App
                </GitHubLink>
              </details>
            )}
            {status.install_url && (
              <GitHubLink url={status.install_url}>
                Install the App on selected repositories
              </GitHubLink>
            )}
            <details className="advanced-token">
              <summary>
                Use a fine-grained access token <KeyRound size={14} />
              </summary>
              <form className="form-stack" onSubmit={connectToken}>
                <p>
                  Limit the token to selected repositories. Read access to
                  metadata and Actions is needed to browse. Actions write access
                  is required only to dispatch workflows.
                </p>
                <label>
                  GitHub access token
                  <input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    required
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="github_pat_…"
                  />
                  <small>
                    Encrypted on the server. Cleared from this input after
                    submission and never saved in browser storage.
                  </small>
                </label>
                <button className="button" disabled={busy || !token.trim()}>
                  {busy ? (
                    <LoaderCircle size={15} className="spin" />
                  ) : (
                    <KeyRound size={15} />
                  )}
                  Connect with token
                </button>
              </form>
            </details>
          </>
        )}
        <p className="small-note">
          This connects GitHub to your team workspace. It does not create a new
          private user account in CI Guard.
        </p>
      </div>
    </Modal>
  );
}

function ImportDialog({
  connections,
  projects,
  onClose,
  onImported,
}: {
  connections: Connection[];
  projects: Project[];
  onClose: () => void;
  onImported: (project: Project) => void;
}) {
  const [connectionId, setConnectionId] = useState(connections[0]?.id || ""),
    [repositories, setRepositories] = useState<Repository[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [search, setSearch] = useState(""),
    [importing, setImporting] = useState<number | null>(null);
  useEffect(() => {
    if (!connectionId) return;
    let active = true;
    setLoading(true);
    setError("");
    githubApi
      .repositories(connectionId)
      .then((r) => {
        if (active) setRepositories(r);
      })
      .catch((e) => {
        if (active) {
          setError(message(e));
          setRepositories([]);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [connectionId]);
  const filtered = useMemo(
    () =>
      repositories.filter((r) =>
        `${r.full_name} ${r.description || ""}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [repositories, search],
  );
  async function importRepository(repo: Repository) {
    if (importing !== null) return;
    setImporting(repo.id);
    setError("");
    try {
      const p = await githubApi.importProject(connectionId, repo.id);
      onImported(p);
    } catch (e) {
      setError(message(e));
    } finally {
      setImporting(null);
    }
  }
  return (
    <Modal
      wide
      title="Import a repository"
      description="Only repositories accessible to this GitHub connection are listed."
      onClose={onClose}
    >
      <div className="import-toolbar">
        <label className="search-input">
          <Search size={17} />
          <input
            aria-label="Search GitHub repositories"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search your GitHub repositories…"
          />
        </label>
        <select
          aria-label="GitHub connection"
          value={connectionId}
          onChange={(e) => setConnectionId(e.target.value)}
          disabled={importing !== null}
        >
          {connections.map((c) => (
            <option value={c.id} key={c.id}>
              {c.login} · {c.auth_type}
            </option>
          ))}
        </select>
      </div>
      {error && <ErrorNotice error={error} />}{" "}
      {loading ? (
        <Loading>Checking repository access on GitHub…</Loading>
      ) : filtered.length ? (
        <div className="repository-list">
          {filtered.map((r) => {
            const imported = projects.some((p) => p.repository_id === r.id);
            return (
              <div key={r.id}>
                <span className="repo-list-icon">
                  <FolderGit2 size={21} />
                </span>
                <div>
                  <strong>{r.full_name}</strong>
                  <p>{r.description || "No repository description."}</p>
                  <small>
                    {r.private ? "Private" : "Public"} · {r.default_branch}
                  </small>
                </div>
                <button
                  className={`button small ${imported ? "" : "dark"}`}
                  disabled={imported || importing !== null}
                  onClick={() => importRepository(r)}
                >
                  {importing === r.id ? (
                    <LoaderCircle size={14} className="spin" />
                  ) : imported ? (
                    <Check size={14} />
                  ) : (
                    <Plus size={14} />
                  )}{" "}
                  {imported ? "Imported" : "Import"}
                </button>
              </div>
            );
          })}
        </div>
      ) : (
        <Empty
          title={
            search ? "No matching repositories" : "No accessible repositories"
          }
          description={
            search
              ? "Try a different name."
              : "Check your GitHub App installation or token repository permissions."
          }
        />
      )}
      <div className="table-footer">
        <span>{filtered.length} repositories shown</span>
        <span>Live GitHub metadata</span>
      </div>
    </Modal>
  );
}

function ProjectDetail({
  project,
  revision,
  notify,
}: {
  project: Project;
  revision: number;
  notify: (message: string) => void;
}) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]),
    [runs, setRuns] = useState<RemoteRun[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [localRevision, setLocalRevision] = useState(0),
    [workflowId, setWorkflowId] = useState(""),
    [ref, setRef] = useState(project.default_branch),
    [inputsText, setInputsText] = useState(""),
    [confirmDispatch, setConfirmDispatch] = useState(false),
    [dispatching, setDispatching] = useState(false),
    [dispatchError, setDispatchError] = useState(""),
    [acceptedUrl, setAcceptedUrl] = useState(""),
    [tokenOpen, setTokenOpen] = useState(false);
  const dispatchLock = useRef(false);
  const closeDispatch = useCallback(() => {
      if (!dispatchLock.current) {
        setConfirmDispatch(false);
        setDispatchError("");
      }
    }, []),
    closeToken = useCallback(() => setTokenOpen(false), []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([githubApi.workflows(project.id), githubApi.runs(project.id)])
      .then(([w, r]) => {
        if (active) {
          setWorkflows(w);
          setRuns(r);
          setError("");
        }
      })
      .catch((e) => {
        if (active) {
          setError(message(e));
          setWorkflows([]);
          setRuns([]);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [project.id, revision, localRevision]);
  const selectedWorkflow = workflows.find((w) => String(w.id) === workflowId);
  function prepareDispatch(e: FormEvent) {
    e.preventDefault();
    setDispatchError("");
    try {
      if (inputsText.trim()) {
        const value = JSON.parse(inputsText);
        if (
          !value ||
          Array.isArray(value) ||
          typeof value !== "object" ||
          Object.values(value).some((v) => typeof v !== "string")
        )
          throw new Error(
            "Workflow inputs must be a JSON object with string values.",
          );
      }
      setConfirmDispatch(true);
    } catch (e) {
      setDispatchError(message(e));
    }
  }
  async function dispatch() {
    if (!selectedWorkflow || dispatchLock.current) return;
    dispatchLock.current = true;
    setDispatching(true);
    setDispatchError("");
    setAcceptedUrl("");
    try {
      const result = await githubApi.dispatch(project.id, {
        workflow_id: selectedWorkflow.id,
        ref: ref.trim(),
        ...(inputsText.trim() ? { inputs: JSON.parse(inputsText) } : {}),
      });
      setAcceptedUrl(result.html_url || `${project.html_url}/actions`);
      setConfirmDispatch(false);
      setLocalRevision((n) => n + 1);
      notify(
        "GitHub accepted the workflow dispatch. Check GitHub for its actual result.",
      );
    } catch (e) {
      setDispatchError(
        `${message(e)} No automatic retry was attempted. Check GitHub Actions before dispatching again.`,
      );
    } finally {
      setDispatching(false);
      dispatchLock.current = false;
    }
  }
  return (
    <section className="project-detail">
      <div className="selected-project-heading">
        <span className="selected-project-icon">
          <GitBranch size={22} />
        </span>
        <div>
          <p className="eyebrow">SELECTED PROJECT</p>
          <h2>{project.full_name}</h2>
        </div>
        <button className="button" onClick={() => setTokenOpen(true)}>
          <KeyRound size={15} />
          Set up CI Guard
        </button>
      </div>
      <div className="project-detail-layout">
        <section className="card github-runs-card">
          <div className="card-header">
            <div>
              <h2>GitHub workflow runs</h2>
              <p className="section-description muted">
                Actual runs reported by GitHub.
              </p>
            </div>
            <button
              className="icon-button"
              onClick={() => setLocalRevision((n) => n + 1)}
              aria-label="Refresh GitHub workflow runs"
              disabled={loading}
            >
              <RefreshCw size={16} className={loading ? "spin" : ""} />
            </button>
          </div>
          {error ? (
            <ErrorNotice error={error} />
          ) : loading ? (
            <Loading />
          ) : runs.length ? (
            <div className="remote-runs">
              {runs.slice(0, 12).map((run) => (
                <div className="remote-run" key={run.id}>
                  <span
                    className={`remote-run-icon ${run.conclusion === "success" ? "success" : run.conclusion === "failure" ? "failure" : "pending"}`}
                  >
                    {run.conclusion === "success" ? (
                      <CheckCircle2 size={19} />
                    ) : run.conclusion === "failure" ? (
                      <AlertTriangle size={19} />
                    ) : (
                      <CircleDashed size={19} />
                    )}
                  </span>
                  <div className="remote-run-name">
                    <GitHubLink url={run.html_url}>
                      {run.name || `Workflow run #${run.run_number}`}
                    </GitHubLink>
                    <small>
                      <GitBranch size={11} />
                      {run.head_branch} · {run.event} · #{run.run_number}
                    </small>
                  </div>
                  <div className="remote-run-status">
                    <span
                      className={`badge ${run.conclusion === "success" ? "success" : run.conclusion === "failure" ? "danger" : "neutral"}`}
                    >
                      {run.conclusion || run.status}
                    </span>
                    <time title={formatDate(run.created_at)}>
                      {relativeTime(run.created_at)}
                    </time>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="No workflow runs yet"
              description="GitHub has not returned any workflow runs for this repository. Configure a workflow, then run it on GitHub."
            />
          )}
          <div className="remote-source">
            <Github size={13} />
            <span>GitHub Actions · {project.full_name}</span>
            <GitHubLink url={`${project.html_url}/actions`}>
              Open Actions
            </GitHubLink>
          </div>
        </section>
        <section className="card dispatch-card">
          <div className="card-header">
            <h2>Run a workflow</h2>
            <span className="icon-disc">
              <Play size={17} />
            </span>
          </div>
          <p className="section-description muted">
            Start an existing GitHub workflow. It must support{" "}
            <code>workflow_dispatch</code>.
          </p>
          <form className="form-stack" onSubmit={prepareDispatch}>
            <label>
              Workflow
              <select
                required
                value={workflowId}
                onChange={(e) => setWorkflowId(e.target.value)}
                disabled={loading}
              >
                <option value="">Select a workflow</option>
                {workflows.map((w) => (
                  <option
                    key={w.id}
                    value={w.id}
                    disabled={w.state !== "active"}
                  >
                    {w.name}
                    {w.state !== "active" ? ` (${w.state})` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Branch or tag
              <input
                required
                value={ref}
                onChange={(e) => setRef(e.target.value)}
                placeholder={project.default_branch}
              />
            </label>
            <details className="workflow-inputs">
              <summary>Optional workflow inputs</summary>
              <label>
                Inputs as JSON
                <textarea
                  value={inputsText}
                  onChange={(e) => setInputsText(e.target.value)}
                  placeholder={'{"environment": "test"}'}
                  rows={4}
                />
              </label>
            </details>
            {dispatchError && !confirmDispatch && (
              <ErrorNotice error={dispatchError} />
            )}
            <button
              className="button dark"
              disabled={!workflowId || !ref.trim() || dispatching}
            >
              <Play size={15} />
              Review & run on GitHub
            </button>
          </form>
          {acceptedUrl && (
            <div className="dispatch-accepted">
              <CheckCircle2 size={18} />
              <div>
                <strong>Dispatch accepted</strong>
                <p>
                  A new run may take a moment to appear. This is not a test
                  result.
                </p>
                <GitHubLink url={acceptedUrl}>See GitHub Actions</GitHubLink>
              </div>
            </div>
          )}
          <p className="small-note">
            This creates a real remote run and may use billable runner minutes.
            CI Guard never retries a dispatch automatically.
          </p>
        </section>
      </div>
      {confirmDispatch && selectedWorkflow && (
        <Modal
          title="Run this workflow on GitHub?"
          description="This action starts a real remote workflow."
          onClose={closeDispatch}
        >
          <dl className="dispatch-confirm-details">
            <div>
              <dt>Repository</dt>
              <dd>{project.full_name}</dd>
            </div>
            <div>
              <dt>Workflow</dt>
              <dd>{selectedWorkflow.name}</dd>
            </div>
            <div>
              <dt>Branch / tag</dt>
              <dd>{ref}</dd>
            </div>
          </dl>
          <div className="quiet-note">
            <Info size={16} />
            <span>
              The workflow runs with its configured permissions and may consume
              billable runner minutes.
            </span>
          </div>
          {dispatchError && <ErrorNotice error={dispatchError} />}
          <div className="modal-actions">
            <button
              className="button"
              onClick={closeDispatch}
              disabled={dispatching}
            >
              Cancel
            </button>
            <button
              className="button dark"
              onClick={dispatch}
              disabled={dispatching}
            >
              {dispatching ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Play size={16} />
              )}
              Run on GitHub
            </button>
          </div>
        </Modal>
      )}
      {tokenOpen && (
        <ProjectSetupDialog
          project={project}
          onClose={closeToken}
          notify={notify}
        />
      )}
    </section>
  );
}

function ProjectSetupDialog({
  project,
  onClose,
  notify,
}: {
  project: Project;
  onClose: () => void;
  notify: (message: string) => void;
}) {
  const [dependencies, setDependencies] = useState<Dependency[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [loading, setLoading] = useState(true),
    [creating, setCreating] = useState(false),
    [error, setError] = useState(""),
    [credential, setCredential] = useState<ProjectRunnerToken | null>(null),
    [showToken, setShowToken] = useState(false),
    [copied, setCopied] = useState(false),
    [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    let active = true;
    api
      .dependencies()
      .then((d) => {
        if (active) setDependencies(d);
      })
      .catch((e) => {
        if (active) setError(message(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  const snippet = liveDiagnosticWorkflow(selected[0]);
  async function create() {
    if (!selected.length || !confirmed) return;
    setCreating(true);
    setError("");
    setCredential(null);
    setCopied(false);
    setShowToken(false);
    try {
      setCredential(await githubApi.createRunnerToken(project.id, selected));
    } catch (e) {
      setError(message(e));
    } finally {
      setCreating(false);
    }
  }
  async function copyToken() {
    if (!credential) return;
    try {
      await navigator.clipboard.writeText(credential.token);
      setCopied(true);
    } catch {
      setError(
        "Clipboard access is unavailable. Reveal and copy the token manually.",
      );
    }
  }
  async function copyWorkflow() {
    try {
      await navigator.clipboard.writeText(snippet);
      notify("Diagnostic workflow copied. Set your test command and dependency.");
    } catch {
      setError(
        "Clipboard access is unavailable. Select and copy the workflow text.",
      );
    }
  }
  return (
    <Modal
      wide
      title="Set up a live diagnostic"
      description={project.full_name}
      onClose={onClose}
    >
      <div className="project-setup">
        <div className="setup-step">
          <span className="setup-number">1</span>
          <div>
            <h3>Create a project runner token</h3>
            <p>
              The token can report runs only for this repository and the
              dependencies you select. It cannot administer GitHub or change
              protection policies.
            </p>
          </div>
        </div>
        {error && <ErrorNotice error={error} />}{" "}
        {loading ? (
          <Loading>Loading registered dependencies…</Loading>
        ) : dependencies.length ? (
          <fieldset className="dependency-checkboxes">
            <legend>Allowed dependencies</legend>
            {dependencies.map((d) => (
              <label key={d.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(d.id)}
                  disabled={!!credential}
                  onChange={(e) =>
                    setSelected((ids) =>
                      e.target.checked
                        ? [...ids, d.id]
                        : ids.filter((id) => id !== d.id),
                    )
                  }
                />
                <span>
                  <strong>{d.name}</strong>
                  <small>{d.id}</small>
                </span>
              </label>
            ))}
          </fieldset>
        ) : (
          <div className="quiet-note">
            <Info size={16} />
            <span>
              Register a dependency on the Dependencies screen before creating a
              runner token.
            </span>
          </div>
        )}
        {!credential ? (
          <>
            <label className="rotation-confirm">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>
                I understand this replaces any existing runner token for this
                project.
              </span>
            </label>
            <button
              className="button dark"
              disabled={!selected.length || !confirmed || creating}
              onClick={create}
            >
              {creating ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <KeyRound size={16} />
              )}
              Create project token
            </button>
          </>
        ) : (
          <div className="one-time-token">
            <div>
              <KeyRound size={20} />
              <span>
                <strong>Save this token now.</strong>
                <small>
                  It is shown only in this dialog and disappears when you close
                  it.
                </small>
              </span>
            </div>
            <label>
              Project runner token
              <input
                type={showToken ? "text" : "password"}
                readOnly
                value={credential.token}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <div className="token-actions">
              <button className="button" onClick={copyToken}>
                {copied ? <Check size={15} /> : <Copy size={15} />}{" "}
                {copied ? "Copied" : "Copy token"}
              </button>
              <button
                className="text-button"
                onClick={() => setShowToken((v) => !v)}
              >
                {showToken ? "Hide token" : "Reveal token"}
              </button>
            </div>
            <p>
              Add it to <strong>{project.full_name}</strong> as the GitHub
              Actions secret <code>CI_GUARD_PROJECT_TOKEN</code>. CI Guard does
              not create GitHub secrets for you.
            </p>
          </div>
        )}
        <div className="settings-divider" />
        <div className="setup-step">
          <span className="setup-number">2</span>
          <div>
            <h3>Add the manual diagnostic</h3>
            <p>
              Set the repository variable <code>CI_GUARD_URL</code> to a
              coordinator URL your runner can reach. Run this example manually
              on a trusted protected branch or tag.
            </p>
          </div>
          <button
            className="icon-button"
            aria-label="Copy project workflow"
            onClick={copyWorkflow}
          >
            <Copy size={17} />
          </button>
        </div>
        <pre className="code-block">
          <code>{snippet}</code>
        </pre>
        <div className="setup-step">
          <span className="setup-number">3</span>
          <div>
            <h3>Configure separate merge and release checks</h3>
            <p>
              Importing a project or copying this example does not enable
              protection. Follow the <GitHubLink url={splitCiDocs}>CI Guard README</GitHubLink>{" "}
              for the isolated merge check and strict live release check.
            </p>
            <GitHubLink url={`${project.html_url}/settings/secrets/actions`}>
              Open repository secrets
            </GitHubLink>
          </div>
        </div>
        <div className="quiet-note">
          <ShieldCheck size={17} />
          <span>
            Keep project tokens out of untrusted fork workflows. Connecting
            GitHub never silently changes repository settings, workflows, or
            secrets.
          </span>
        </div>
      </div>
    </Modal>
  );
}
