export type RunStatus =
  | "queued"
  | "running"
  | "passed"
  | "failed"
  | "blocked"
  | "error"
  | "cancelled";
export type HealthStatus = "healthy" | "unhealthy" | "unknown";
export type PolicyMode = "off" | "observe" | "enforce";
export type Classification =
  | "passed"
  | "assertion"
  | "dependency"
  | "unknown"
  | "timeout";

export interface Policy {
  mode: PolicyMode;
  max_job_retries: number;
  shared_dependency_retry_budget: number;
  dependency_cooldown_seconds: number;
  source: string;
  fingerprint?: string | null;
  error?: string | null;
  repository?: string;
}

export type PolicyInput = Pick<
  Policy,
  | "mode"
  | "max_job_retries"
  | "shared_dependency_retry_budget"
  | "dependency_cooldown_seconds"
>;

export interface Decision {
  action: "run" | "retry" | "block" | "stop" | "probe";
  reason: string;
  retry_after_seconds?: number | null;
  shared_remaining?: number | null;
  would_block?: boolean;
  policy?: Policy;
  id?: number | string;
  created_at?: string;
  probe_token?: string;
  probe_generation?: number;
}

export interface Attempt {
  id?: number | string;
  attempt_number: number;
  exit_code: number;
  duration_seconds: number;
  classification: Classification;
  evidence?: string | Record<string, unknown> | unknown[] | null;
  health_status?: HealthStatus;
  health_detail?: string | null;
  created_at?: string;
  started_at?: string;
  finished_at?: string | null;
  idempotency_key?: string;
}

export interface Run {
  id: string;
  repository: string;
  dependency: string;
  job_id: string;
  workflow_run_id?: string | null;
  status: RunStatus;
  created_at: string;
  finished_at?: string | null;
  attempt_count: number;
  prevented_attempts: number;
  would_prevent_attempts: number;
  duration_seconds?: number | null;
  reason?: string | null;
  policy: Policy;
  decision?: Decision | null;
  demo: boolean;
  attempts?: Attempt[];
  decisions?: Decision[];
  command?: string[] | string | null;
}

export interface Dependency {
  id: string;
  name: string;
  health_url: string;
  error_patterns: string[];
  timeout_seconds: number;
  status: HealthStatus;
  retries_used: number;
  budget: number;
  next_probe_at?: string | null;
  last_checked_at?: string | null;
  active_runs: number;
  health_detail?: string | null;
  opened_at?: string | null;
  window_open?: boolean;
  probe_in_flight?: boolean;
  shadow_retries_used?: number;
}

export interface DependencyInput {
  id: string;
  name: string;
  health_url: string;
  error_patterns: string[];
  timeout_seconds?: number;
}

export interface DependencyProbe {
  status: HealthStatus;
  detail?: string | null;
  health_status?: HealthStatus;
  health_detail?: string | null;
  dependency?: Dependency | string;
  checked_at?: string;
  performed?: boolean;
  decision?: Decision;
  [key: string]: unknown;
}

export interface Overview {
  stats: {
    total_runs: number;
    passed_runs: number;
    failed_runs: number;
    blocked_runs: number;
    prevented_attempts: number;
    would_prevent_attempts: number;
    execution_seconds: number;
  };
  trend: Array<{ date: string; attempts: number; prevented: number }>;
  recent_runs: Run[];
  dependencies: Dependency[];
  policy: Policy;
  demo_mode: boolean;
  policy_source: string;
}

export interface Health {
  status: string;
  version: string;
  demo_mode: boolean;
}

export interface Settings {
  demo_mode?: boolean;
  version?: string;
  auth_required?: boolean;
  authentication_enabled?: boolean;
  authentication_required?: boolean;
  token_configured?: boolean;
  flagsmith_configured?: boolean;
  flagsmith_connected?: boolean;
  flagsmith_url?: string;
  policy_source?: string;
  policy_writable?: boolean;
  server_url?: string;
  database?: { engine: string; mode: string; replicas: number };
  limitations?: string[];
  installation?: { server_url: string; action_ref: string };
  [key: string]: unknown;
}

export interface AuditEntry {
  id?: number | string;
  created_at?: string;
  timestamp?: string;
  action?: string;
  event?: string;
  message?: string;
  actor?: string;
  repository?: string;
  run_id?: string;
  detail?: string | Record<string, unknown> | null;
  details?: string | Record<string, unknown> | null;
  [key: string]: unknown;
}

export type Audit = AuditEntry;

export interface RunFilters {
  status?: RunStatus | "";
  repository?: string;
  search?: string;
  limit?: number;
}

export type DemoScenario = "outage" | "healthy" | "assertion";

export interface DemoResult {
  scenario?: DemoScenario;
  status?: string;
  message?: string;
  run_ids?: string[];
  runs?: Run[];
  results?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}
