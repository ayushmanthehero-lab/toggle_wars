import { afterEach, describe, expect, it, vi } from "vitest";
import {
  formatDate,
  formatDuration,
  formatNumber,
  formatTime,
  relativeTime,
  statusLabel,
  statusTone,
} from "./format";
import { api, ApiError, getToken, setToken } from "./api";

describe("evidence formatting", () => {
  it("does not turn absent or invalid measurements into zero", () => {
    for (const value of [
      undefined,
      null,
      Number.NaN,
      Number.POSITIVE_INFINITY,
    ]) {
      expect(formatNumber(value)).toBe("—");
      expect(formatDuration(value)).toBe("—");
    }
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(1234)).toBe("1,234");
    expect(formatDuration(-1)).toBe("—");
  });

  it("shows actual short durations without rounding them up to a minute", () => {
    expect(formatDuration(0)).toBe("0s");
    expect(formatDuration(0.125)).toBe("125ms");
    expect(formatDuration(12.34)).toBe("12.3s");
    expect(formatDuration(59.99)).toBe("59.9s");
    expect(formatDuration(61)).toBe("1m 1s");
    expect(formatDuration(3661)).toBe("1h 1m");
  });

  it("uses UTC consistently, including timestamps with a non-UTC offset", () => {
    expect(formatDate("2026-11-21T01:30:00+02:00")).toBe("Nov 20, 2026");
    expect(formatTime("2026-11-21T01:30:00+02:00")).toBe("23:30 UTC");
    expect(formatDate("not-a-date")).toBe("—");
    expect(formatDate(null)).toBe("—");
    expect(formatTime("")).toBe("—");
  });

  it("makes future cooldown times distinct from previous events", () => {
    const now = Date.parse("2026-11-21T12:00:00Z");
    expect(relativeTime("2026-11-21T11:58:00Z", now)).toBe("2m ago");
    expect(relativeTime("2026-11-21T12:02:00Z", now)).toBe("in 2m");
    expect(relativeTime("2026-11-21T12:00:00Z", now)).toBe("just now");
    expect(relativeTime("invalid", now)).toBe("—");
  });

  it("keeps unknown statuses neutral rather than implying success", () => {
    expect(statusTone("passed")).toBe("success");
    expect(statusTone("healthy")).toBe("success");
    expect(statusTone("failed")).toBe("danger");
    expect(statusTone("blocked")).toBe("warning");
    expect(statusTone("running")).toBe("info");
    expect(statusTone("unexpected")).toBe("neutral");
    expect(statusLabel(undefined)).toBe("Unknown");
    expect(statusLabel("would_block")).toBe("Would block");
  });
});

describe("API contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("encodes filters and sends the current session token without putting it in the URL", async () => {
    const data = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => data.set(key, value),
      removeItem: (key: string) => data.delete(key),
    });
    setToken("  session-secret  ");
    expect(getToken()).toBe("session-secret");
    let requestUrl = "";
    let requestHeaders = new Headers();
    vi.stubGlobal("fetch", async (url: string, options: RequestInit) => {
      requestUrl = url;
      requestHeaders = new Headers(options.headers);
      return new Response("[]", {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    expect(
      await api.runs({
        repository: "acme/orders",
        search: "a & b",
        status: "failed",
      }),
    ).toEqual([]);
    const url = new URL(requestUrl, "https://ci.example");
    expect(url.pathname).toBe("/api/runs");
    expect(url.searchParams.get("repository")).toBe("acme/orders");
    expect(url.searchParams.get("search")).toBe("a & b");
    expect(url.searchParams.get("status")).toBe("failed");
    expect(requestUrl).not.toContain("session-secret");
    expect(requestHeaders.get("authorization")).toBe("Bearer session-secret");
    setToken("");
    expect(getToken()).toBe("");
    expect(data.has("ci-guard-token")).toBe(false);
  });

  it("preserves an authorization failure status and the server explanation", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(JSON.stringify({ detail: "Invalid token" }), {
          status: 401,
          headers: { "content-type": "application/json" },
        }),
    );
    try {
      await api.overview();
      throw new Error("Request unexpectedly succeeded");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 401, detail: "Invalid token" });
    }
  });

  it("gives a useful error for an HTML reverse-proxy failure without exposing the page markup", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response("<html>private proxy details</html>", {
          status: 502,
          statusText: "Bad Gateway",
          headers: { "content-type": "text/html" },
        }),
    );
    await expect(api.health()).rejects.toMatchObject({
      status: 502,
      detail: "Bad Gateway",
    });
  });
});
