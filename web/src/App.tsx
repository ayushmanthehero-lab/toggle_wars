import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  ShieldCheck,
  FolderGit2,
  LayoutDashboard,
  GitPullRequest,
  Network,
  SlidersHorizontal,
  Settings2,
  ArrowUpRight,
  ArrowRight,
  Plus,
  Search,
  RefreshCw,
  Check,
  Clock3,
  CircleCheck,
  CircleAlert,
  CircleDashed,
  Download,
  Copy,
  Play,
  FlaskConical,
  X,
  Activity,
  BookOpen,
  KeyRound,
  Save,
  Eye,
  ShieldOff,
  GitBranch,
  LoaderCircle,
  Info,
  Server,
  CheckCheck,
} from "lucide-react";
import { api, getToken, setToken } from "./lib/api";
import type {
  Run,
  Policy,
  Dependency,
  Overview,
  Settings,
  AuditEntry,
} from "./lib/types";
import {
  formatDuration,
  formatNumber,
  formatDate,
  formatTime,
  relativeTime,
  statusLabel,
  statusTone,
} from "./lib/format";
import Projects from "./components/Projects";
import { summarizeDemo } from "./lib/demo";
import { liveDiagnosticWorkflow, splitCiDocs } from "./lib/liveDiagnostic";
import { Modal } from "./components/Modal";
import { TrendChart, OutcomeChart } from "./components/Charts";

type Page =
  | "overview"
  | "runs"
  | "projects"
  | "dependencies"
  | "policies"
  | "settings";
const pages = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "runs", label: "Runs", icon: GitPullRequest },
  { id: "projects", label: "Projects", icon: FolderGit2 },
  { id: "dependencies", label: "Dependencies", icon: Network },
  { id: "policies", label: "Policies", icon: SlidersHorizontal },
  { id: "settings", label: "Settings", icon: Settings2 },
] as const;
const defaultPolicy: Policy = {
  mode: "observe",
  max_job_retries: 2,
  shared_dependency_retry_budget: 2,
  dependency_cooldown_seconds: 30,
  source: "unavailable",
  fingerprint: "",
};
function getPage(): Page {
  const hash = location.hash.slice(1);
  return pages.some((p) => p.id === hash) ? (hash as Page) : "overview";
}
function errorText(error: unknown) {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}
function Badge({ status }: { status: string }) {
  return (
    <span className={`badge ${statusTone(status)}`}>
      <i />
      {statusLabel(status)}
    </span>
  );
}
function Empty({
  title,
  description,
  icon,
  action,
}: {
  title: string;
  description: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon || <GitPullRequest size={26} />}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
function ErrorBox({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-box" role="alert">
      <CircleAlert size={20} />
      <div>
        <strong>We couldn’t complete that request.</strong>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button className="button small" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
function Loading({ label = "Loading recorded activity…" }: { label?: string }) {
  return (
    <div className="loading-state" role="status">
      <LoaderCircle className="spin" size={24} />
      <span>{label}</span>
    </div>
  );
}
function CardHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="card-header">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h2>{title}</h2>
      </div>
      {children}
    </div>
  );
}
function RunTable({
  runs,
  onSelect,
  compact = false,
}: {
  runs: Run[];
  onSelect: (id: string) => void;
  compact?: boolean;
}) {
  return (
    <div className="table-scroll">
      <table className="run-table">
        <thead>
          <tr>
            <th>Workflow / repository</th>
            <th>Status</th>
            {!compact && <th>Dependency</th>}
            <th>Attempts</th>
            <th>Duration</th>
            <th>Started</th>
            <th>
              <span className="sr-only">Details</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <button className="table-run" onClick={() => onSelect(run.id)}>
                  <span className={`run-icon ${statusTone(run.status)}`}>
                    <GitBranch size={17} />
                  </span>
                  <span>
                    <strong>{run.job_id}</strong>
                    <small>
                      {run.repository}
                      {run.demo && <span className="inline-demo">demo</span>}
                    </small>
                  </span>
                </button>
              </td>
              <td>
                <Badge status={run.status} />
              </td>
              {!compact && (
                <td>
                  <span className="mono muted">{run.dependency}</span>
                </td>
              )}
              <td>
                <span className="attempt-number">{run.attempt_count}</span>
              </td>
              <td>{formatDuration(run.duration_seconds)}</td>
              <td>
                <time
                  title={`${formatDate(run.created_at)} · ${formatTime(run.created_at)}`}
                >
                  {relativeTime(run.created_at)}
                </time>
              </td>
              <td>
                <button
                  className="icon-button small"
                  aria-label={`View ${run.job_id}`}
                  onClick={() => onSelect(run.id)}
                >
                  <ArrowUpRight size={17} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(getPage);
  const mainRef = useRef<HTMLElement>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true),
    [refreshing, setRefreshing] = useState(false),
    [error, setError] = useState("");
  const [revision, setRevision] = useState(0),
    [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [runId, setRunId] = useState<string | null>(null),
    [demoOpen, setDemoOpen] = useState(false),
    [toast, setToast] = useState("");
  const notify = useCallback((message: string) => setToast(message), []);
  const navigate = useCallback((next: Page) => {
    location.hash = next;
    setPage(next);
  }, []);
  const refresh = useCallback(() => setRevision((x) => x + 1), []);
  const closeRun = useCallback(() => setRunId(null), []),
    closeDemo = useCallback(() => setDemoOpen(false), []);
  useEffect(() => {
    const listener = () => setPage(getPage());
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, []);
  useEffect(() => {
    let active = true;
    setRefreshing(true);
    Promise.all([api.overview(), api.settings()])
      .then(([o, s]) => {
        if (active) {
          setOverview(o);
          setSettings(s);
          setError("");
          setLastUpdated(new Date());
        }
      })
      .catch((e) => {
        if (active) {
          setError(errorText(e));
          setSettings(null);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
          setRefreshing(false);
        }
      });
    return () => {
      active = false;
    };
  }, [revision]);
  useEffect(() => {
    const timer = window.setInterval(refresh, 15000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  const titles: Record<
    Page,
    { kicker: string; title: string; description: string }
  > = {
    overview: {
      kicker: "A LITTLE LESS NOISE. A LOT MORE SIGNAL.",
      title: "Your CI, under control.",
      description:
        "Keep useful tests running. Give broken dependencies a break.",
    },
    runs: {
      kicker: "EVERY ATTEMPT, ACCOUNTED FOR.",
      title: "Workflow runs",
      description:
        "The full story behind every pass, failure, and blocked attempt.",
    },
    projects: {
      kicker: "YOUR REPOSITORIES. ONE SHARED WORKSPACE.",
      title: "Projects, connected.",
      description:
        "Connect GitHub. Choose repositories. Protect the work that matters.",
    },
    dependencies: {
      kicker: "SHARED SERVICES. SHARED LIMITS.",
      title: "Dependency health",
      description: "One outage shouldn’t become a hundred repeated failures.",
    },
    policies: {
      kicker: "YOUR RULES. ONE SOURCE OF TRUTH.",
      title: "Protection policies",
      description: "Roll out smarter retries with self-hosted Flagsmith.",
    },
    settings: {
      kicker: "BUILT TO FIT YOUR WORKFLOW.",
      title: "Workspace settings",
      description:
        "Connect your repositories and keep your installation healthy.",
    },
  };
  const demo = overview?.demo_mode ?? settings?.demo_mode ?? false;
  return (
    <>
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          // The fragment belongs to the route. Move focus without replacing it.
          event.preventDefault();
          mainRef.current?.focus();
        }}
      >
        Skip to content
      </a>
      <div className="app-shell">
        <header className="topbar">
          <a href="#overview" className="brand" aria-label="CI Guard overview">
            <span className="brand-mark">
              <ShieldCheck size={23} />
            </span>
            <span>
              ci<span className="brand-guard">guard</span>
              <span className="brand-dot">.</span>
            </span>
          </a>
          <nav className="topnav" aria-label="Main navigation">
            {pages
              .filter((p) => p.id !== "settings")
              .map((p) => (
                <button
                  key={p.id}
                  aria-current={page === p.id ? "page" : undefined}
                  className={page === p.id ? "active" : ""}
                  onClick={() => navigate(p.id)}
                >
                  {p.label}
                </button>
              ))}
          </nav>
          <div className="topbar-actions">
            <span className="edition">
              <i className={`dot ${error ? "pink" : "green"}`} />
              {page === "projects"
                ? "GitHub · live"
                : demo
                  ? "Demo workspace"
                  : "Self-hosted"}
            </span>
            <button
              className={`icon-button ${page === "settings" ? "selected" : ""}`}
              aria-label="Workspace settings"
              onClick={() => navigate("settings")}
            >
              <Settings2 size={19} />
            </button>
            <div className="workspace-avatar" title="CI Guard workspace">
              CG
            </div>
          </div>
        </header>
        <div className="app-body">
          <aside className="rail" aria-label="Workspace navigation">
            {pages.map((p) => (
              <button
                key={p.id}
                className={page === p.id ? "active" : ""}
                title={p.label}
                aria-label={p.label}
                aria-current={page === p.id ? "page" : undefined}
                onClick={() => navigate(p.id)}
              >
                <p.icon size={21} />
              </button>
            ))}
            <div className="rail-divider" />
            <button
              title="Installation guide"
              aria-label="Installation guide"
              onClick={() => {
                navigate("settings");
                setTimeout(
                  () =>
                    document
                      .getElementById("installation")
                      ?.scrollIntoView({ behavior: "smooth" }),
                  100,
                );
              }}
            >
              <BookOpen size={20} />
            </button>
            <div className="rail-bottom">
              <span className="rail-version">v1</span>
            </div>
          </aside>
          <main id="main" className="main-content" tabIndex={-1} ref={mainRef}>
            <div className="page-heading">
              <div>
                <p className="eyebrow">{titles[page].kicker}</p>
                <h1>{titles[page].title}</h1>
                <p className="page-description">{titles[page].description}</p>
                {page === "overview" && (
                  <a href="/demo-3d/">Open the 3D Outage Lab <span aria-hidden="true">↗</span></a>
                )}
              </div>
              <div className="heading-actions">
                <button
                  className="icon-button refresh-button"
                  title="Refresh recorded data"
                  aria-label="Refresh recorded data"
                  onClick={refresh}
                  disabled={refreshing}
                >
                  <RefreshCw size={18} className={refreshing ? "spin" : ""} />
                </button>
                {demo && page !== "projects" && (
                  <button
                    className="button dark"
                    onClick={() => setDemoOpen(true)}
                  >
                    <Play size={15} fill="currentColor" />
                    Run a demo
                  </button>
                )}
                {page === "overview" && (
                  <button
                    className="button dark"
                    onClick={() => navigate("projects")}
                  >
                    <Plus size={17} />
                    Connect a repository
                  </button>
                )}
              </div>
            </div>
            {error && (
              <div className="connection-error">
                <ErrorBox message={error} onRetry={refresh} />
                <button
                  className="text-button"
                  onClick={() => navigate("settings")}
                >
                  Check connection settings <ArrowRight size={15} />
                </button>
              </div>
            )}
            {loading && page === "overview" ? (
              <Loading />
            ) : page === "overview" && overview ? (
              <OverviewPage
                data={overview}
                navigate={navigate}
                onSelect={setRunId}
                onDemo={() => setDemoOpen(true)}
              />
            ) : page === "overview" ? (
              <Empty
                title="Your workspace is not connected"
                description="Connect to the coordinator to see real CI activity. Open settings to supply your workspace access token."
                icon={<Server size={28} />}
                action={
                  <button
                    className="button dark"
                    onClick={() => navigate("settings")}
                  >
                    Open settings <ArrowRight size={16} />
                  </button>
                }
              />
            ) : null}
            {page === "runs" && (
              <RunsPage revision={revision} onSelect={setRunId} />
            )}{" "}
            {page === "dependencies" && (
              <DependenciesPage
                revision={revision}
                refresh={refresh}
                notify={notify}
              />
            )}{" "}
            {page === "policies" && (
              <PoliciesPage
                revision={revision}
                refresh={refresh}
                notify={notify}
                settings={settings}
              />
            )}{" "}
            {page === "projects" && (
              <Projects
                revision={revision}
                notify={notify}
                onSettings={() => navigate("settings")}
              />
            )}
            {page === "settings" && (
              <SettingsPage
                settings={settings}
                refresh={refresh}
                notify={notify}
              />
            )}
            <footer className="footer">
              <span>
                <ShieldCheck size={14} />
                Every attempt counts.
              </span>
              <div>
                {demo && page !== "projects" && (
                  <span className="demo-caption">
                    Controlled demo · not production traffic
                  </span>
                )}
                <span>
                  {page === "projects"
                    ? "Live GitHub records · never simulated"
                    : lastUpdated
                      ? `Updated ${lastUpdated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                      : "Waiting for coordinator"}
                </span>
              </div>
            </footer>
          </main>
        </div>
      </div>
      {runId && <RunDetail id={runId} onClose={closeRun} />}{" "}
      {demoOpen && (
        <DemoModal onClose={closeDemo} refresh={refresh} notify={notify} />
      )}{" "}
      {toast && (
        <div className="toast" role="status">
          <CircleCheck size={18} />
          {toast}
          <button
            className="icon-button small"
            aria-label="Dismiss notification"
            onClick={() => setToast("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
    </>
  );
}

function OverviewPage({
  data,
  navigate,
  onSelect,
  onDemo,
}: {
  data: Overview;
  navigate: (p: Page) => void;
  onSelect: (id: string) => void;
  onDemo: () => void;
}) {
  const s = data.stats;
  const total = s.total_runs || 0;
  const passed = s.passed_runs || 0;
  const failed = s.failed_runs || 0;
  const blocked = s.blocked_runs || 0;
  const policy = data.policy || defaultPolicy;
  const healthy = data.dependencies.filter(
    (d) => d.status === "healthy",
  ).length;
  return (
    <div className="page-content">
      <div
        className={`protection-strip ${policy.mode === "enforce" ? "protected" : ""}`}
      >
        <span className="protection-icon">
          {policy.mode === "enforce" ? (
            <ShieldCheck size={19} />
          ) : policy.mode === "observe" ? (
            <Eye size={19} />
          ) : (
            <ShieldOff size={19} />
          )}
        </span>
        <div>
          <strong>
            {policy.mode === "enforce"
              ? "Protection is on"
              : policy.mode === "observe"
                ? "Observe first. Protect with confidence."
                : "Protection is off"}
          </strong>
          <span>
            {policy.mode === "enforce"
              ? "Jobs share a retry budget when a dependency fails."
              : policy.mode === "observe"
                ? "CI Guard records what it would block, without enforcing shared limits."
                : "Commands run once. No automatic infrastructure retries."}
          </span>
        </div>
        <button className="text-button" onClick={() => navigate("policies")}>
          View policy <ArrowUpRight size={16} />
        </button>
      </div>
      <div className="stat-grid">
        <StatCard
          title="Total workflow runs"
          value={formatNumber(total)}
          icon={<GitPullRequest size={23} />}
          color="blue"
          caption={`${passed} passed · ${blocked} blocked`}
          onClick={() => navigate("runs")}
        />
        <StatCard
          title="Attempts prevented"
          value={formatNumber(s.prevented_attempts)}
          icon={<ShieldCheck size={23} />}
          color="green"
          caption={
            s.would_prevent_attempts
              ? `${s.would_prevent_attempts} additional would-block decisions`
              : "Actual blocked attempts, not an estimate"
          }
          onClick={() => navigate("runs")}
        />
        <StatCard
          title="Test execution time"
          value={formatDuration(s.execution_seconds)}
          icon={<Clock3 size={23} />}
          color="pink"
          caption="Measured across recorded test attempts"
          onClick={() => navigate("runs")}
        />
      </div>
      <div className="analytics-grid">
        <section className="card activity-card">
          <CardHeader
            eyebrow="LESS REPETITION, MORE PROGRESS"
            title="Attempt activity"
          >
            <span className="subtle-pill">
              Recorded history <Clock3 size={12} />
            </span>
          </CardHeader>
          <div className="chart-legend">
            <span>
              <i className="dot blue" />
              Executed attempts
            </span>
            <span>
              <i className="dot pink" />
              Prevented attempts
            </span>
          </div>
          <TrendChart points={data.trend || []} />
          <div className="chart-footer">
            <span>
              <Activity size={14} />
              Actual executions and enforcement decisions
            </span>
            <span>UTC</span>
          </div>
        </section>
        <section className="card">
          <CardHeader title="Run outcomes">
            <span className="icon-disc">
              <GitBranch size={18} />
            </span>
          </CardHeader>
          <OutcomeChart
            passed={passed}
            failed={failed}
            blocked={blocked}
            total={total}
          />
          <div className="quiet-note">
            <Info size={14} />
            <span>Blocked tests still block merging.</span>
          </div>
        </section>
      </div>
      <div className="lower-grid">
        <section className="card dependency-overview">
          <CardHeader title="Dependency pulse">
            <button
              className="icon-button"
              aria-label="View dependencies"
              onClick={() => navigate("dependencies")}
            >
              <ArrowUpRight size={18} />
            </button>
          </CardHeader>
          <div className="dependency-headline">
            <span>
              <strong>{healthy}</strong> / {data.dependencies.length}
            </span>
            <p>services confirmed healthy</p>
          </div>
          {data.dependencies.length ? (
            <div className="pulse-list">
              {data.dependencies.slice(0, 3).map((d) => (
                <button key={d.id} onClick={() => navigate("dependencies")}>
                  <span
                    className={`pulse-icon ${d.status === "healthy" ? "green" : d.status === "unhealthy" ? "pink" : "gray"}`}
                  >
                    <Network size={17} />
                  </span>
                  <span className="pulse-name">
                    <strong>{d.name}</strong>
                    <small>
                      {d.status === "unhealthy"
                        ? "Needs attention"
                        : d.status === "healthy"
                          ? "Ready for test traffic"
                          : "Awaiting a health check"}
                    </small>
                  </span>
                  <span
                    className={`status-dot ${d.status === "healthy" ? "green" : d.status === "unhealthy" ? "pink" : "gray"}`}
                  />
                </button>
              ))}
            </div>
          ) : (
            <div className="compact-empty">
              <p>No dependencies registered yet.</p>
              <button
                className="text-button"
                onClick={() => navigate("dependencies")}
              >
                Add a dependency <Plus size={14} />
              </button>
            </div>
          )}
        </section>
        <section className="card recent-card">
          <CardHeader title="Recent runs">
            <button className="text-button" onClick={() => navigate("runs")}>
              View all <ArrowRight size={15} />
            </button>
          </CardHeader>
          {data.recent_runs.length ? (
            <RunTable
              compact
              runs={data.recent_runs.slice(0, 5)}
              onSelect={onSelect}
            />
          ) : (
            <Empty
              title="A clean slate"
              description="Your first protected test run will appear here with its result, evidence, and retry decisions."
              icon={<GitPullRequest size={24} />}
              action={
                data.demo_mode ? (
                  <button className="button" onClick={onDemo}>
                    <Play size={14} />
                    Try the controlled demo
                  </button>
                ) : undefined
              }
            />
          )}
        </section>
      </div>
    </div>
  );
}
function StatCard({
  title,
  value,
  icon,
  color,
  caption,
  onClick,
}: {
  title: string;
  value: string;
  icon: ReactNode;
  color: string;
  caption: string;
  onClick: () => void;
}) {
  return (
    <section className="card stat-card">
      <div className="stat-top">
        <span className={`stat-icon ${color}`}>{icon}</span>
        <h2>{title}</h2>
        <button
          className="icon-button"
          aria-label={`View ${title.toLowerCase()}`}
          onClick={onClick}
        >
          <ArrowUpRight size={18} />
        </button>
      </div>
      <div className="stat-value">{value}</div>
      <p className="stat-caption">{caption}</p>
    </section>
  );
}

function RunsPage({
  revision,
  onSelect,
}: {
  revision: number;
  onSelect: (id: string) => void;
}) {
  const [runs, setRuns] = useState<Run[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [search, setSearch] = useState(""),
    [status, setStatus] = useState(""),
    [repository, setRepository] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .runs({ limit: 200 })
      .then((r) => {
        if (active) {
          setRuns(r);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(errorText(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);
  const repositories = [...new Set(runs.map((r) => r.repository))];
  const filtered = useMemo(
    () =>
      runs.filter(
        (r) =>
          (!status || r.status === status) &&
          (!repository || r.repository === repository) &&
          `${r.job_id} ${r.repository} ${r.dependency} ${r.id}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      ),
    [runs, status, repository, search],
  );
  return (
    <section className="card runs-card">
      <div className="list-toolbar">
        <label className="search-input">
          <Search size={18} />
          <input
            aria-label="Search runs"
            placeholder="Search runs, repositories, dependencies…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button
              className="icon-button small"
              aria-label="Clear search"
              onClick={() => setSearch("")}
            >
              <X size={15} />
            </button>
          )}
        </label>
        <div className="filter-row">
          <select
            aria-label="Filter by repository"
            value={repository}
            onChange={(e) => setRepository(e.target.value)}
          >
            <option value="">All repositories</option>
            {repositories.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <select
            aria-label="Filter by run status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">All statuses</option>
            {[
              "passed",
              "failed",
              "blocked",
              "running",
              "error",
              "cancelled",
            ].map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error ? (
        <ErrorBox message={error} />
      ) : loading ? (
        <Loading />
      ) : filtered.length ? (
        <RunTable runs={filtered} onSelect={onSelect} />
      ) : (
        <Empty
          title={runs.length ? "No matching runs" : "No workflow runs yet"}
          description={
            runs.length
              ? "Try another search or remove a filter."
              : "Connect the GitHub Action or run the controlled demo to record your first test execution."
          }
          action={
            runs.length ? (
              <button
                className="button"
                onClick={() => {
                  setSearch("");
                  setStatus("");
                  setRepository("");
                }}
              >
                Clear filters
              </button>
            ) : undefined
          }
        />
      )}
      <div className="table-footer">
        <span>
          {filtered.length} of {runs.length} loaded runs
        </span>
        <span>Latest 200 records · results refresh automatically</span>
      </div>
    </section>
  );
}

function RunDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [run, setRun] = useState<Run | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .run(id)
      .then((r) => {
        if (active) setRun(r);
      })
      .catch((e) => {
        if (active) setError(errorText(e));
      });
    return () => {
      active = false;
    };
  }, [id]);
  const download = () => {
    if (!run) return;
    const blob = new Blob([JSON.stringify(run, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ci-guard-run-${run.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Modal
      wide
      title={run?.job_id || "Run details"}
      description={
        run
          ? `${run.repository} · ${run.dependency}`
          : "Loading the recorded run…"
      }
      onClose={onClose}
    >
      {error ? (
        <ErrorBox message={error} />
      ) : !run ? (
        <Loading />
      ) : (
        <>
          <div className="run-detail-top">
            <Badge status={run.status} />
            <span className="mono muted">{run.id}</span>
            <button className="button small" onClick={download}>
              <Download size={15} />
              Export JSON
            </button>
          </div>
          <div className="detail-stat-grid">
            <div>
              <span>Attempts executed</span>
              <strong>{run.attempt_count}</strong>
            </div>
            <div>
              <span>Attempts prevented</span>
              <strong>{run.prevented_attempts}</strong>
            </div>
            <div>
              <span>Execution time</span>
              <strong>{formatDuration(run.duration_seconds)}</strong>
            </div>
          </div>
          {run.reason && (
            <div
              className={`decision-note ${run.status === "passed" ? "success" : ""}`}
            >
              <Info size={18} />
              <div>
                <strong>Why this result?</strong>
                <p>{run.reason}</p>
              </div>
            </div>
          )}
          <div className="detail-section">
            <h3>Attempt timeline</h3>
            {run.attempts?.length ? (
              <div className="timeline">
                {run.attempts.map((attempt, index) => (
                  <div className="timeline-item" key={attempt.id || index}>
                    <span
                      className={`timeline-dot ${attempt.classification === "passed" ? "green" : "pink"}`}
                    />
                    <div>
                      <div className="timeline-heading">
                        <strong>
                          Attempt {attempt.attempt_number || index + 1}
                        </strong>
                        <Badge status={attempt.classification} />
                        <span>{formatDuration(attempt.duration_seconds)}</span>
                      </div>
                      <p>Exit code {attempt.exit_code}</p>
                      {attempt.evidence && (
                        <pre className="evidence">
                          {typeof attempt.evidence === "string"
                            ? attempt.evidence
                            : JSON.stringify(attempt.evidence, null, 2)}
                        </pre>
                      )}
                      {attempt.health_status && (
                        <small className="muted">
                          Dependency health: {attempt.health_status}
                        </small>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">
                The test command did not execute for this run.
              </p>
            )}
          </div>
          <div className="detail-section">
            <h3>Policy applied</h3>
            <div className="policy-summary-grid">
              <div>
                <span>Mode</span>
                <strong>{run.policy?.mode || "Unknown"}</strong>
              </div>
              <div>
                <span>Job retry limit</span>
                <strong>{run.policy?.max_job_retries ?? "—"}</strong>
              </div>
              <div>
                <span>Shared retry budget</span>
                <strong>
                  {run.policy?.shared_dependency_retry_budget ?? "—"}
                </strong>
              </div>
              <div>
                <span>Cooldown</span>
                <strong>
                  {run.policy?.dependency_cooldown_seconds ?? "—"}s
                </strong>
              </div>
            </div>
            <p className="small-note">
              Source: {run.policy?.source || "Not recorded"} ·{" "}
              {formatDate(run.created_at)}
            </p>
          </div>
          {run.decisions?.length ? (
            <div className="detail-section">
              <h3>Decision history</h3>
              {run.decisions.map((d, index) => (
                <div className="decision-row" key={index}>
                  <span className="subtle-pill">{d.action}</span>
                  <p>{d.reason}</p>
                  {d.would_block && (
                    <span className="badge amber">Observe only</span>
                  )}
                </div>
              ))}
            </div>
          ) : null}
          {run.status === "blocked" && (
            <div className="quiet-note">
              <ShieldCheck size={18} />
              <span>
                Tests have not passed. Restore the dependency, then rerun your
                workflow. This run must remain a failing required check.
              </span>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

function DependenciesPage({
  revision,
  refresh,
  notify,
}: {
  revision: number;
  refresh: () => void;
  notify: (s: string) => void;
}) {
  const [items, setItems] = useState<Dependency[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [editing, setEditing] = useState<Dependency | null | undefined>(undefined),
    [probing, setProbing] = useState<string | null>(null);
  const close = useCallback(() => setEditing(undefined), []);
  useEffect(() => {
    let active = true;
    api
      .dependencies()
      .then((d) => {
        if (active) {
          setItems(d);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(errorText(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);
  async function probe(id: string) {
    setProbing(id);
    setError("");
    try {
      const result = await api.probe(id);
      notify(`Health check complete: ${result.status || "recorded"}`);
      refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setProbing(null);
    }
  }
  return (
    <div className="page-content">
      <div className="section-toolbar">
        <span className="muted">
          {items.length} registered{" "}
          {items.length === 1 ? "dependency" : "dependencies"}
        </span>
        <button className="button dark" onClick={() => setEditing(null)}>
          <Plus size={17} />
          Add dependency
        </button>
      </div>
      {error && <ErrorBox message={error} />}{" "}
      {loading ? (
        <Loading />
      ) : !items.length ? (
        <section className="card">
          <Empty
            title="Give your tests a safety net"
            description="Register a service your tests depend on, its health endpoint, and the exact errors that indicate an outage."
            icon={<Network size={30} />}
            action={
              <button className="button dark" onClick={() => setEditing(null)}>
                <Plus size={16} />
                Add your first dependency
              </button>
            }
          />
        </section>
      ) : (
        <div className="dependency-grid">
          {items.map((d) => (
            <section className="card dependency-card" key={d.id}>
              <div className="dependency-title">
                <span
                  className={`large-service-icon ${d.status === "healthy" ? "green" : d.status === "unhealthy" ? "pink" : "blue"}`}
                >
                  <Network size={25} />
                </span>
                <Badge status={d.status} />
              </div>
              <h2>{d.name}</h2>
              <p className="mono muted dependency-id">{d.id}</p>
              <div className="endpoint">
                <Activity size={15} />
                <span title={d.health_url}>{d.health_url}</span>
              </div>
              <div className="budget-label">
                <span>Shared retry allowance</span>
                <strong>
                  {d.retries_used ?? 0} <span>/ {d.budget ?? "—"} used</span>
                </strong>
              </div>
              <div className="progress-track">
                <div
                  className={d.status === "unhealthy" ? "pink" : "blue"}
                  style={{
                    width: `${Math.min(((d.retries_used || 0) / Math.max(d.budget || 1, 1)) * 100, 100)}%`,
                  }}
                />
              </div>
              <div className="dependency-meta">
                <span>
                  <Clock3 size={14} />
                  {d.last_checked_at
                    ? relativeTime(d.last_checked_at)
                    : "Not checked yet"}
                </span>
                <span>{d.active_runs ?? 0} active runs</span>
              </div>
              {d.next_probe_at && d.status === "unhealthy" && (
                <p className="small-note">
                  Recovery check eligible: {formatTime(d.next_probe_at)}
                </p>
              )}
              <div className="card-actions">
                <button
                  className="button"
                  onClick={() => probe(d.id)}
                  disabled={probing === d.id}
                >
                  {probing === d.id ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Activity size={16} />
                  )}
                  Check health
                </button>
                <button
                  className="icon-button"
                  aria-label={`Edit ${d.name}`}
                  onClick={() => setEditing(d)}
                >
                  <SlidersHorizontal size={18} />
                </button>
              </div>
            </section>
          ))}
        </div>
      )}
      <div className="info-panel">
        <ShieldCheck size={22} />
        <div>
          <h3>Evidence before enforcement</h3>
          <p>
            CI Guard requires a recognized dependency error and an unhealthy
            health check before it spends the shared retry allowance. Ordinary
            assertion failures stay visible.
          </p>
        </div>
      </div>
      {editing !== undefined && (
        <DependencyForm
          dependency={editing}
          onClose={close}
          onSaved={() => {
            refresh();
            notify(editing ? "Dependency updated" : "Dependency registered");
            close();
          }}
        />
      )}
    </div>
  );
}

function DependencyForm({
  dependency,
  onClose,
  onSaved,
}: {
  dependency: Dependency | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [id, setId] = useState(dependency?.id || ""),
    [name, setName] = useState(dependency?.name || ""),
    [url, setUrl] = useState(dependency?.health_url || ""),
    [patterns, setPatterns] = useState(
      dependency?.error_patterns?.join("\n") || "",
    ),
    [timeout, setTimeoutValue] = useState(dependency?.timeout_seconds || 3),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      await api.saveDependency(
        {
          id,
          name,
          health_url: url,
          error_patterns: patterns
            .split("\n")
            .map((p) => p.trim())
            .filter(Boolean),
          timeout_seconds: timeout,
        },
        dependency?.id,
      );
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      title={dependency ? "Edit dependency" : "Register a dependency"}
      description="Connect a shared service to the tests that depend on it."
      onClose={onClose}
    >
      <form onSubmit={submit} className="form-stack">
        {error && <ErrorBox message={error} />}
        <div className="form-two">
          <label>
            Dependency ID
            <input
              required
              pattern="[a-zA-Z0-9_-]+"
              disabled={!!dependency}
              value={id}
              placeholder="inventory-api"
              onChange={(e) => setId(e.target.value)}
            />
            <small>Use this ID in your GitHub Action.</small>
          </label>
          <label>
            Display name
            <input
              required
              value={name}
              placeholder="Inventory service"
              onChange={(e) => setName(e.target.value)}
            />
          </label>
        </div>
        <label>
          Health endpoint
          <input
            required
            type="url"
            value={url}
            placeholder="http://inventory:9000/health"
            onChange={(e) => setUrl(e.target.value)}
          />
          <small>The coordinator must be able to reach this endpoint.</small>
        </label>
        <label>
          Recognized error signatures
          <textarea
            required
            rows={4}
            value={patterns}
            placeholder={"INVENTORY_UNAVAILABLE\nConnection refused"}
            onChange={(e) => setPatterns(e.target.value)}
          />
          <small>
            One exact text signature per line. Choose specific infrastructure
            errors.
          </small>
        </label>
        <label>
          Health check timeout (seconds)
          <input
            required
            type="number"
            min="1"
            max="30"
            value={timeout}
            onChange={(e) => setTimeoutValue(Number(e.target.value))}
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button dark" disabled={saving}>
            {saving ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Check size={16} />
            )}{" "}
            {dependency ? "Save changes" : "Register dependency"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function PoliciesPage({
  revision,
  refresh,
  notify,
  settings,
}: {
  revision: number;
  refresh: () => void;
  notify: (s: string) => void;
  settings: Settings | null;
}) {
  const dirty = useRef(false);
  const [policy, setPolicy] = useState<Policy | null>(null),
    [draft, setDraft] = useState(defaultPolicy),
    [loading, setLoading] = useState(true),
    [saving, setSaving] = useState(false),
    [error, setError] = useState(""),
    [repository, setRepository] = useState(""),
    [preview, setPreview] = useState<Policy | null>(null),
    [previewLoading, setPreviewLoading] = useState(false);
  useEffect(() => {
    let active = true;
    api
      .policy()
      .then((p) => {
        if (active) {
          setPolicy(p);
          if (!dirty.current) setDraft(p);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(errorText(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);
  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const saved = await api.savePolicy(draft);
      dirty.current = false;
      setPolicy(saved);
      setDraft(saved);
      notify("Policy saved. New decisions will use these settings.");
      refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }
  async function evaluate(e: FormEvent) {
    e.preventDefault();
    setPreviewLoading(true);
    setError("");
    try {
      setPreview(await api.policy(repository));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPreviewLoading(false);
    }
  }
  const updateDraft = (update: (p: Policy) => Policy) => {
    dirty.current = true;
    setDraft(update);
  };
  const changed =
    policy &&
    [
      "mode",
      "max_job_retries",
      "shared_dependency_retry_budget",
      "dependency_cooldown_seconds",
    ].some((k) => draft[k as keyof Policy] !== policy[k as keyof Policy]);
  return (
    <div className="page-content">
      {error && <ErrorBox message={error} />}{" "}
      {loading ? (
        <Loading label="Reading the current Flagsmith policy…" />
      ) : policy ? (
        <>
          <div className="policy-source-bar">
            <span className="flagsmith-mark">
              <SlidersHorizontal size={19} />
            </span>
            <div>
              <strong>
                {policy.source === "demo"
                  ? "Local demo policy"
                  : policy.source?.includes("flagsmith")
                    ? "Connected to Flagsmith"
                    : `Policy source: ${policy.source}`}
              </strong>
              <span>
                {policy.source === "demo"
                  ? "Changes are saved to this controlled demo workspace."
                  : "Policy is evaluated by Flagsmith for each repository before execution decisions."}
              </span>
            </div>
            <span className="subtle-pill mono">
              {policy.fingerprint?.slice(0, 12) || "No fingerprint"}
            </span>
          </div>
          {policy.error && (
            <ErrorBox message={`Policy evaluation: ${policy.error}`} />
          )}
          <div className="policy-layout">
            <form className="card policy-editor" onSubmit={save}>
              <CardHeader title="Protection mode">
                <span className="subtle-pill">Environment default</span>
              </CardHeader>
              <div className="mode-options">
                {[
                  {
                    mode: "off",
                    label: "Off",
                    desc: "Run tests once. No retries.",
                    icon: ShieldOff,
                  },
                  {
                    mode: "observe",
                    label: "Observe",
                    desc: "Record decisions. Learn first.",
                    icon: Eye,
                  },
                  {
                    mode: "enforce",
                    label: "Enforce",
                    desc: "Apply the shared retry budget.",
                    icon: ShieldCheck,
                  },
                ].map((m) => (
                  <button
                    type="button"
                    key={m.mode}
                    aria-pressed={draft.mode === m.mode}
                    className={`mode-option ${draft.mode === m.mode ? "active" : ""}`}
                    onClick={() =>
                      updateDraft((d) => ({
                        ...d,
                        mode: m.mode as Policy["mode"],
                      }))
                    }
                  >
                    <span>
                      <m.icon size={20} />
                      {draft.mode === m.mode && <CircleCheck size={16} />}
                    </span>
                    <strong>{m.label}</strong>
                    <small>{m.desc}</small>
                  </button>
                ))}
              </div>
              <div className="settings-divider" />
              <h3>Retry boundaries</h3>
              <p className="muted section-description">
                Only recognized dependency failures can receive retries.
              </p>
              <PolicyField
                title="Retries per job"
                help="Maximum extra attempts one job can request."
                value={draft.max_job_retries}
                min={0}
                max={10}
                onChange={(v) =>
                  updateDraft((d) => ({ ...d, max_job_retries: v }))
                }
              />
              <PolicyField
                title="Shared dependency budget"
                help="Total retries available across jobs in one failure window."
                value={draft.shared_dependency_retry_budget}
                min={0}
                max={100}
                onChange={(v) =>
                  updateDraft((d) => ({
                    ...d,
                    shared_dependency_retry_budget: v,
                  }))
                }
              />
              <PolicyField
                title="Recovery cooldown"
                help="Seconds before another recovery health check is eligible."
                value={draft.dependency_cooldown_seconds}
                min={1}
                max={3600}
                suffix="sec"
                onChange={(v) =>
                  updateDraft((d) => ({ ...d, dependency_cooldown_seconds: v }))
                }
              />
              {settings?.policy_writable === false && (
                <div className="quiet-note">
                  <KeyRound size={17} />
                  <span>
                    This connection is read-only. Configure the Flagsmith
                    management credentials on the server to edit policies here.
                  </span>
                </div>
              )}
              <div className="editor-footer">
                <span className="muted">
                  {changed ? "You have unsaved changes." : "All changes saved."}
                </span>
                <button
                  type="submit"
                  className="button dark"
                  disabled={
                    !changed || saving || settings?.policy_writable === false
                  }
                >
                  {saving ? (
                    <LoaderCircle size={16} className="spin" />
                  ) : (
                    <Save size={16} />
                  )}
                  Save policy
                </button>
              </div>
            </form>
            <div className="policy-sidebar">
              <section className="card">
                <CardHeader title="Repository preview" />
                <p className="muted section-description">
                  Check which policy applies after Flagsmith targeting rules.
                </p>
                <form onSubmit={evaluate} className="form-stack">
                  <label>
                    Repository
                    <input
                      required
                      value={repository}
                      placeholder="your-team/your-repo"
                      pattern="[^\s/]+/[^\s/]+"
                      onChange={(e) => setRepository(e.target.value)}
                    />
                  </label>
                  <button className="button" disabled={previewLoading}>
                    {previewLoading ? (
                      <LoaderCircle size={15} className="spin" />
                    ) : (
                      <Eye size={16} />
                    )}
                    Evaluate policy
                  </button>
                </form>
                {preview && (
                  <div className="preview-result">
                    <span className="eyebrow">EFFECTIVE POLICY</span>
                    <strong>{statusLabel(preview.mode)}</strong>
                    <dl>
                      <div>
                        <dt>Per-job retries</dt>
                        <dd>{preview.max_job_retries}</dd>
                      </div>
                      <div>
                        <dt>Shared budget</dt>
                        <dd>{preview.shared_dependency_retry_budget}</dd>
                      </div>
                      <div>
                        <dt>Cooldown</dt>
                        <dd>{preview.dependency_cooldown_seconds}s</dd>
                      </div>
                    </dl>
                    {preview.error && (
                      <p className="error-text">{preview.error}</p>
                    )}
                  </div>
                )}
              </section>
              <section className="guide-card">
                <span className="guide-icon">
                  <ShieldCheck size={23} />
                </span>
                <h3>Start with observe.</h3>
                <p>
                  See what CI Guard would prevent before turning on enforcement
                  for your team.
                </p>
                <div className="guide-step">
                  <span>1</span>Observe existing test behavior
                </div>
                <div className="guide-step">
                  <span>2</span>Target one repository in Flagsmith
                </div>
                <div className="guide-step">
                  <span>3</span>Enable enforcement when ready
                </div>
              </section>
            </div>
          </div>
        </>
      ) : (
        <Empty
          title="Policy unavailable"
          description="Check your connection and server-side Flagsmith configuration."
          icon={<SlidersHorizontal size={28} />}
        />
      )}
    </div>
  );
}
function PolicyField({
  title,
  help,
  value,
  min,
  max,
  suffix,
  onChange,
}: {
  title: string;
  help: string;
  value: number;
  min: number;
  max: number;
  suffix?: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="policy-field">
      <span>
        <strong>{title}</strong>
        <small>{help}</small>
      </span>
      <span className="number-input">
        <input
          aria-label={title}
          type="number"
          required
          min={min}
          max={max}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        {suffix && <span>{suffix}</span>}
      </span>
    </label>
  );
}

function SettingsPage({
  settings,
  refresh,
  notify,
}: {
  settings: Settings | null;
  refresh: () => void;
  notify: (s: string) => void;
}) {
  const [token, setTokenInput] = useState(getToken()),
    [showToken, setShowToken] = useState(false),
    [checking, setChecking] = useState(false),
    [connectionError, setConnectionError] = useState(""),
    [audit, setAudit] = useState<AuditEntry[]>([]),
    [auditError, setAuditError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .audit()
      .then((a) => {
        if (active) setAudit(a);
      })
      .catch((e) => {
        if (active) setAuditError(errorText(e));
      });
    return () => {
      active = false;
    };
  }, [settings]);
  const install = liveDiagnosticWorkflow("inventory-api");
  async function connect(e: FormEvent) {
    e.preventDefault();
    setChecking(true);
    setConnectionError("");
    setToken(token);
    try {
      await api.settings();
      refresh();
      notify("Connection verified. Token saved for this browser tab.");
    } catch (e) {
      setConnectionError(errorText(e));
    } finally {
      setChecking(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(install);
      notify("Diagnostic workflow copied. Set your test command and dependency.");
    } catch {
      notify("Clipboard unavailable. Select and copy the workflow text.");
    }
  }
  const database = settings?.database;
  const databaseInfo =
    typeof database === "object" && database !== null
      ? (database as Record<string, unknown>)
      : null;
  return (
    <div className="page-content">
      <div className="settings-grid">
        <section className="card">
          <CardHeader title="Workspace connection">
            <span className="icon-disc">
              <Server size={18} />
            </span>
          </CardHeader>
          <div className="connection-status">
            <i className={`status-dot ${settings ? "green" : "pink"}`} />
            <div>
              <strong>
                {settings ? "Coordinator connected" : "Connection required"}
              </strong>
              <span>{location.origin}/api</span>
            </div>
            <span className="subtle-pill">
              {settings?.version ? `v${settings.version}` : "Unknown version"}
            </span>
          </div>
          <form className="form-stack" onSubmit={connect}>
            <label>
              Workspace access token
              <div className="password-input">
                <input
                  type={showToken ? "text" : "password"}
                  autoComplete="off"
                  value={token}
                  placeholder={
                    settings?.demo_mode
                      ? "Optional in local demo mode"
                      : "Enter your CI_GUARD_ADMIN_TOKEN"
                  }
                  onChange={(e) => setTokenInput(e.target.value)}
                />
                <button
                  type="button"
                  aria-label={
                    showToken ? "Hide access token" : "Show access token"
                  }
                  className="icon-button"
                  onClick={() => setShowToken((x) => !x)}
                >
                  <Eye size={17} />
                </button>
              </div>
              <small>
                Stored for this browser session. Use an administrator token to
                edit policies or dependencies.
              </small>
            </label>
            {connectionError && <ErrorBox message={connectionError} />}
            <div className="card-actions">
              <button className="button dark" disabled={checking}>
                {checking ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <CheckCheck size={16} />
                )}
                Save & verify
              </button>
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setToken("");
                  setTokenInput("");
                  refresh();
                  notify("Session token cleared.");
                }}
              >
                Clear token
              </button>
            </div>
          </form>
        </section>
        <section className="card">
          <CardHeader title="Installation health" />
          <div className="health-items">
            <HealthItem
              label="Coordinator"
              value={settings ? "Connected" : "Unknown"}
              state={!!settings}
            />
            <HealthItem
              label="Flagsmith configuration"
              value={
                settings?.flagsmith_configured
                  ? "Configured"
                  : settings?.demo_mode
                    ? "Local demo policy"
                    : "Not configured"
              }
              state={!!settings?.flagsmith_configured || !!settings?.demo_mode}
            />
            <HealthItem
              label="Policy editing"
              value={settings?.policy_writable ? "Available" : "Read-only"}
              state={!!settings?.policy_writable}
            />
            <HealthItem
              label="Database"
              value={
                databaseInfo
                  ? `${String(databaseInfo.engine || "SQLite").toUpperCase()} · ${String(databaseInfo.mode || "WAL").toUpperCase()}`
                  : "SQLite · local storage"
              }
              state={!!settings}
            />
          </div>
          <div className="quiet-note">
            <Info size={15} />
            <span>
              One coordinator, one persistent database. Keep production access
              behind HTTPS.
            </span>
          </div>
        </section>
      </div>
      <section className="card installation-card" id="installation">
        <CardHeader
          eyebrow="ADD TO YOUR REPOSITORY"
          title="Run a live diagnostic."
        >
          <button className="button" onClick={copy}>
            <Copy size={16} />
            Copy workflow
          </button>
        </CardHeader>
        <p className="muted section-description">
          Register a dependency and configure its project runner token in
          GitHub. Run this example manually on a trusted protected ref, not as
          a merge or release gate.
        </p>
        <pre className="code-block">
          <code>{install}</code>
        </pre>
        <div className="installation-notes">
          <div>
            <span>01</span>
            <p>
              <strong>Make the coordinator reachable.</strong> Your GitHub
              runner needs network access to CI Guard and your test
              dependencies.
            </p>
          </div>
          <div>
            <span>02</span>
            <p>
              <strong>Use separate checks.</strong> Require isolated tests for
              merges and the strict live check for releases. See the{" "}
              <a href={splitCiDocs} target="_blank" rel="noreferrer">
                CI Guard README
              </a>
              .
            </p>
          </div>
          <div>
            <span>03</span>
            <p>
              <strong>Protect fork contributions.</strong> Do not expose
              workspace secrets to untrusted pull requests.
            </p>
          </div>
        </div>
      </section>
      <section className="card">
        <CardHeader title="Recent configuration activity" />
        <p className="muted section-description">
          Recorded changes and coordinator events.
        </p>
        {auditError ? (
          <p className="muted">Activity unavailable: {auditError}</p>
        ) : audit.length ? (
          <div className="audit-list">
            {audit.slice(0, 8).map((entry, index) => (
              <div key={String(entry.id ?? index)}>
                <span className="icon-disc">
                  <Activity size={16} />
                </span>
                <span>
                  <strong>
                    {entry.action || entry.event || "Configuration event"}
                  </strong>
                  <small>
                    {entry.message ||
                      (typeof entry.detail === "string"
                        ? entry.detail
                        : entry.repository) ||
                      "Recorded by the coordinator"}
                  </small>
                </span>
                <time>{relativeTime(entry.created_at || entry.timestamp)}</time>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">No configuration events recorded yet.</p>
        )}
      </section>
    </div>
  );
}
function HealthItem({
  label,
  value,
  state,
}: {
  label: string;
  value: string;
  state: boolean;
}) {
  return (
    <div className="health-item">
      <span>{label}</span>
      <strong>
        {state ? <CircleCheck size={15} /> : <CircleDashed size={15} />} {value}
      </strong>
    </div>
  );
}

function DemoModal({
  onClose,
  refresh,
  notify,
}: {
  onClose: () => void;
  refresh: () => void;
  notify: (s: string) => void;
}) {
  const [scenario, setScenario] = useState<"outage" | "healthy" | "assertion">(
      "outage",
    ),
    [jobs, setJobs] = useState(4),
    [running, setRunning] = useState(false),
    [error, setError] = useState(""),
    [result, setResult] = useState<Record<string, unknown> | null>(null);
  const summary = summarizeDemo(result);
  async function start(e: FormEvent) {
    e.preventDefault();
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const response = await api.runDemo(scenario, jobs);
      setResult(response);
      refresh();
      notify("Controlled demo completed. Recorded runs are ready to inspect.");
    } catch (e) {
      setError(errorText(e));
      refresh();
    } finally {
      setRunning(false);
    }
  }
  return (
    <Modal
      title="See CI Guard in action"
      description="A controlled local demonstration using real test subprocesses. No production traffic or billing."
      onClose={onClose}
    >
      <form className="form-stack" onSubmit={start}>
        <div className="demo-notice">
          <FlaskConical size={20} />
          <p>
            The demo uses your current policy. Compare observe and enforce modes
            to see the shared budget at work.
          </p>
        </div>
        <fieldset className="scenario-options">
          <legend>Choose a scenario</legend>
          {[
            {
              id: "outage",
              title: "Dependency outage",
              desc: "Concurrent jobs reach an unavailable service.",
              icon: Network,
            },
            {
              id: "assertion",
              title: "A genuine test failure",
              desc: "The dependency is healthy. An assertion fails.",
              icon: CircleAlert,
            },
            {
              id: "healthy",
              title: "Healthy recovery",
              desc: "Restore the service and run passing tests.",
              icon: CircleCheck,
            },
          ].map((s) => (
            <label key={s.id} className={scenario === s.id ? "selected" : ""}>
              <input
                type="radio"
                name="scenario"
                value={s.id}
                checked={scenario === s.id}
                onChange={() => setScenario(s.id as typeof scenario)}
              />
              <s.icon size={20} />
              <span>
                <strong>{s.title}</strong>
                <small>{s.desc}</small>
              </span>
            </label>
          ))}
        </fieldset>
        <label>
          Concurrent jobs
          <select
            value={jobs}
            onChange={(e) => setJobs(Number(e.target.value))}
          >
            {[1, 2, 4, 8].map((n) => (
              <option value={n} key={n}>
                {n} {n === 1 ? "job" : "jobs"}
              </option>
            ))}
          </select>
        </label>
        {error && <ErrorBox message={error} />}{" "}
        {result && (
          <div className="decision-note success">
            <CircleCheck size={19} />
            <div>
              <strong>Demo recorded</strong>
              <p>
                {typeof result.message === "string"
                  ? result.message
                  : "Open Workflow runs to inspect actual attempts, evidence, and results."}
              </p>
            </div>
          </div>
        )}
        {summary && (
          <div className="demo-result-panel">
            <p className="eyebrow">MEASURED LOCAL DEMO RESULTS</p>
            <div className="demo-result-stats">
              <div>
                <strong>{summary.jobs}</strong>
                <span>jobs recorded</span>
              </div>
              <div>
                <strong>{summary.attempts ?? "—"}</strong>
                <span>actual test attempts</span>
              </div>
              <div>
                <strong>{formatDuration(summary.executionSeconds)}</strong>
                <span>actual execution time</span>
              </div>
            </div>
            <div className="demo-statuses">
              {Object.entries(summary.statuses).map(([status, count]) => (
                <span className={`badge ${statusTone(status)}`} key={status}>
                  {count} {statusLabel(status).toLowerCase()}
                </span>
              ))}
            </div>
            {!summary.complete && (
              <p className="small-note">
                Some reports are incomplete. Totals are unavailable rather than
                estimated.
              </p>
            )}
            <p className="small-note">
              Actual local subprocess results. These are not GitHub-hosted jobs
              or cash savings.
            </p>
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose}>
            {result ? "View dashboard" : "Close"}
          </button>
          <button className="button dark" disabled={running}>
            {running ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Play size={15} />
            )}{" "}
            {running ? "Running real tests…" : "Run scenario"}
          </button>
        </div>
        {running && (
          <p className="small-note" role="status">
            The coordinator is executing the selected scenario. Results will
            appear when the tests finish.
          </p>
        )}
      </form>
    </Modal>
  );
}
