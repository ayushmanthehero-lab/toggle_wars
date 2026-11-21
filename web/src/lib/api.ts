import type {
  AuditEntry,
  DemoResult,
  DemoScenario,
  Dependency,
  DependencyInput,
  DependencyProbe,
  Health,
  Overview,
  Policy,
  PolicyInput,
  Run,
  RunFilters,
  Settings,
} from "./types";

const TOKEN_KEY = "ci-guard-token";
let memoryToken = "";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function getToken(): string {
  try {
    return typeof sessionStorage === "undefined"
      ? memoryToken
      : (sessionStorage.getItem(TOKEN_KEY) ?? "");
  } catch {
    return memoryToken;
  }
}

export function setToken(value: string): void {
  memoryToken = value.trim();
  try {
    if (typeof sessionStorage === "undefined") return;
    if (memoryToken) sessionStorage.setItem(TOKEN_KEY, memoryToken);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // A browser that disables storage can still authenticate for this page session.
  }
}

function errorDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object" || !("detail" in body)) return fallback;
  const detail = body.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((issue: unknown) => {
      if (
        !issue ||
        typeof issue !== "object" ||
        !("msg" in issue) ||
        typeof issue.msg !== "string"
      )
        return [];
      const location =
        "loc" in issue && Array.isArray(issue.loc)
          ? issue.loc.filter((part) => part !== "body").join(".")
          : "";
      return [`${location ? `${location}: ` : ""}${issue.msg}`];
    });
    if (messages.length) return messages.join("; ");
  }
  return fallback;
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body !== undefined)
    headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Unable to reach CI Guard. Check the server connection.",
    );
  }
  let body: unknown;
  try {
    body = response.status === 204 ? undefined : await response.json();
  } catch {
    if (response.ok)
      throw new ApiError(
        response.status,
        "CI Guard returned an invalid JSON response.",
      );
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      errorDetail(
        body,
        response.statusText || `Request failed (${response.status})`,
      ),
    );
  }
  return body as T;
}

function json(method: "POST" | "PUT", payload?: unknown): RequestInit {
  return {
    method,
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  };
}

export const api = {
  health: () => request<Health>("/health"),
  overview: () => request<Overview>("/overview"),
  runs: (filters: RunFilters = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    return request<Run[]>(`/runs${query.size ? `?${query.toString()}` : ""}`);
  },
  run: (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}`),
  dependencies: () => request<Dependency[]>("/dependencies"),
  saveDependency: (payload: DependencyInput, existingId?: string) =>
    existingId
      ? request<Dependency>(
          `/dependencies/${encodeURIComponent(existingId)}`,
          json("PUT", payload),
        )
      : request<Dependency>("/dependencies", json("POST", payload)),
  probe: (id: string) =>
    request<DependencyProbe>(
      `/dependencies/${encodeURIComponent(id)}/probe`,
      json("POST"),
    ),
  policy: (repository?: string) =>
    request<Policy>(
      `/policy${repository ? `?${new URLSearchParams({ repository })}` : ""}`,
    ),
  savePolicy: (policy: PolicyInput) =>
    request<Policy>(
      "/policy",
      json("PUT", {
        mode: policy.mode,
        max_job_retries: policy.max_job_retries,
        shared_dependency_retry_budget: policy.shared_dependency_retry_budget,
        dependency_cooldown_seconds: policy.dependency_cooldown_seconds,
      }),
    ),
  settings: () => request<Settings>("/settings"),
  audit: () => request<AuditEntry[]>("/audit"),
  runDemo: (scenario: DemoScenario, jobs = 4) =>
    request<DemoResult>("/demo/run", json("POST", { scenario, jobs })),
};
