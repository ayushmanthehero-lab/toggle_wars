import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getToken, setToken } from "./api";
import { githubApi } from "./github";

type CapturedRequest = {
  url: string;
  method: string;
  body: unknown;
  authorization: string | null;
};

describe("GitHub API boundary", () => {
  let calls: CapturedRequest[];
  let sessionData: Map<string, string>;
  let localData: Map<string, string>;

  beforeEach(() => {
    calls = [];
    sessionData = new Map();
    localData = new Map();
    const storage = (data: Map<string, string>) => ({
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => data.set(key, value),
      removeItem: (key: string) => data.delete(key),
    });
    vi.stubGlobal("sessionStorage", storage(sessionData));
    vi.stubGlobal("localStorage", storage(localData));
    setToken("coordinator-admin");
    vi.stubGlobal("fetch", async (url: string, options: RequestInit = {}) => {
      calls.push({
        url,
        method: options.method ?? "GET",
        body: options.body ? JSON.parse(String(options.body)) : undefined,
        authorization: new Headers(options.headers).get("Authorization"),
      });
      return new Response("[]", { status: 200 });
    });
  });

  afterEach(() => {
    setToken("");
    vi.unstubAllGlobals();
  });

  it("keeps connection and project IDs in a single encoded path segment", async () => {
    await githubApi.repositories("account/a?b");
    await githubApi.workflows("project/a?b");
    await githubApi.runs("project/a?b");
    await githubApi.disconnect("account/a?b");
    await githubApi.deleteProject("project/a?b");
    expect(calls.map(({ url, method }) => ({ url, method }))).toEqual([
      {
        url: "/api/github/connections/account%2Fa%3Fb/repositories",
        method: "GET",
      },
      { url: "/api/projects/project%2Fa%3Fb/workflows", method: "GET" },
      { url: "/api/projects/project%2Fa%3Fb/actions/runs", method: "GET" },
      { url: "/api/github/connections/account%2Fa%3Fb", method: "DELETE" },
      { url: "/api/projects/project%2Fa%3Fb", method: "DELETE" },
    ]);
  });

  it("sends account authorization and project selection through the documented bodies", async () => {
    await githubApi.status();
    await githubApi.projects();
    await githubApi.connectToken("github-personal-secret");
    await githubApi.connectEnvironment();
    await githubApi.startDevice();
    await githubApi.pollDevice("authorization-123");
    await githubApi.importProject("connection-123", 7654321);
    expect(
      calls.map(({ url, method, body }) => ({ url, method, body })),
    ).toEqual([
      { url: "/api/github/status", method: "GET", body: undefined },
      { url: "/api/projects", method: "GET", body: undefined },
      {
        url: "/api/github/connect/token",
        method: "POST",
        body: { token: "github-personal-secret" },
      },
      {
        url: "/api/github/connect/environment",
        method: "POST",
        body: undefined,
      },
      { url: "/api/github/device/start", method: "POST", body: undefined },
      {
        url: "/api/github/device/poll",
        method: "POST",
        body: { id: "authorization-123" },
      },
      {
        url: "/api/projects",
        method: "POST",
        body: { connection_id: "connection-123", repository_id: 7654321 },
      },
    ]);
    expect(
      calls.every(
        ({ authorization }) => authorization === "Bearer coordinator-admin",
      ),
    ).toBe(true);
    expect(calls.every(({ url }) => !url.includes("secret"))).toBe(true);
    expect([...sessionData]).toEqual([["ci-guard-token", "coordinator-admin"]]);
    expect(localData.size).toBe(0);
  });

  it("sends workflow refs and inputs as JSON and does not retry an ambiguous dispatch failure", async () => {
    await githubApi.dispatch("project-123", {
      workflow_id: 42,
      ref: "feature/a & b",
      inputs: { scenario: "outage", jobs: "4" },
    });
    expect(calls[0]).toMatchObject({
      url: "/api/projects/project-123/dispatch",
      method: "POST",
      body: {
        workflow_id: 42,
        ref: "feature/a & b",
        inputs: { scenario: "outage", jobs: "4" },
      },
    });
    let attempts = 0;
    vi.stubGlobal("fetch", async () => {
      attempts += 1;
      throw new TypeError("Connection closed after sending request");
    });
    await expect(
      githubApi.dispatch("project-123", { workflow_id: 42, ref: "main" }),
    ).rejects.toMatchObject({ status: 0 });
    expect(attempts).toBe(1);
  });

  it("returns a newly created runner token without persisting it or replacing administrator authentication", async () => {
    const issued = {
      token: "project-runner-secret",
      project_id: "project-123",
      repository: "acme/orders",
      dependency_ids: ["inventory"],
    };
    let requestBody: unknown;
    vi.stubGlobal("fetch", async (_url: string, options: RequestInit) => {
      requestBody = JSON.parse(String(options.body));
      return new Response(JSON.stringify(issued), { status: 200 });
    });
    expect(
      await githubApi.createRunnerToken("project-123", ["inventory"]),
    ).toEqual(issued);
    expect(requestBody).toEqual({ dependency_ids: ["inventory"] });
    expect(getToken()).toBe("coordinator-admin");
    expect([...sessionData]).toEqual([["ci-guard-token", "coordinator-admin"]]);
    expect(localData.size).toBe(0);
  });

  it("preserves strict administrator errors for the locked interface state", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(
          JSON.stringify({ detail: "Administrator token required" }),
          { status: 401 },
        ),
    );
    await expect(githubApi.status()).rejects.toMatchObject({
      status: 401,
      detail: "Administrator token required",
    });
  });
});
