import { request } from "./api";

export interface Connection {
  id: string;
  login: string;
  avatar_url: string;
  html_url: string;
  auth_type: string;
  created_at: string;
  expires_at?: string | null;
}

export interface GitHubStatus {
  device_flow_configured: boolean;
  environment_token_available: boolean;
  app_slug: string | null;
  install_url: string | null;
  connections: Connection[];
  encryption_configured?: boolean;
}

export interface Repository {
  id: number;
  full_name: string;
  name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  description?: string | null;
}

export interface Project extends Omit<Repository, "id"> {
  id: string;
  repository_id: number;
  connection_id: string;
  created_at: string;
}

export interface Workflow {
  id: number;
  name: string;
  path: string;
  state: string;
  html_url: string;
}

export interface RemoteRun {
  id: number;
  name: string | null;
  status: string;
  conclusion: string | null;
  html_url: string;
  head_branch: string | null;
  event: string;
  created_at: string;
  updated_at: string;
  run_number: number;
}

export interface DeviceAuthorization {
  id: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}

export interface DevicePoll {
  status: "pending" | "slow_down" | "connected" | "expired" | "denied";
  interval?: number;
  connection?: Connection;
}

export interface ProjectRunnerToken {
  token: string;
  project_id: string;
  repository: string;
  dependency_ids: string[];
}

export interface WorkflowDispatch {
  workflow_id: number;
  ref: string;
  inputs?: Record<string, string>;
}

export interface DispatchResult {
  status: "accepted";
  html_url: string;
}

function post(payload?: unknown): RequestInit {
  return {
    method: "POST",
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  };
}

export const githubApi = {
  status: () => request<GitHubStatus>("/github/status"),
  connectToken: (token: string) =>
    request<Connection>("/github/connect/token", post({ token })),
  connectEnvironment: () =>
    request<Connection>("/github/connect/environment", post()),
  startDevice: () =>
    request<DeviceAuthorization>("/github/device/start", post()),
  pollDevice: (id: string) =>
    request<DevicePoll>("/github/device/poll", post({ id })),
  disconnect: (id: string) =>
    request<void>(`/github/connections/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  repositories: (connectionId: string) =>
    request<Repository[]>(
      `/github/connections/${encodeURIComponent(connectionId)}/repositories`,
    ),
  projects: () => request<Project[]>("/projects"),
  importProject: (connectionId: string, repositoryId: number) =>
    request<Project>(
      "/projects",
      post({ connection_id: connectionId, repository_id: repositoryId }),
    ),
  deleteProject: (id: string) =>
    request<void>(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
  workflows: (id: string) =>
    request<Workflow[]>(`/projects/${encodeURIComponent(id)}/workflows`),
  runs: (id: string) =>
    request<RemoteRun[]>(`/projects/${encodeURIComponent(id)}/actions/runs`),
  dispatch: (projectId: string, payload: WorkflowDispatch) =>
    request<DispatchResult>(
      `/projects/${encodeURIComponent(projectId)}/dispatch`,
      post(payload),
    ),
  createRunnerToken: (projectId: string, dependencyIds: string[]) =>
    request<ProjectRunnerToken>(
      `/projects/${encodeURIComponent(projectId)}/runner-token`,
      post({ dependency_ids: dependencyIds }),
    ),
};
