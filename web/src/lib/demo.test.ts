import { describe, expect, it } from "vitest";
import { summarizeDemo } from "./demo";

describe("actual demo evidence", () => {
  it("counts executed attempt records rather than requested jobs or retry policy", () => {
    const payload = {
      jobs: 99,
      results: [
        {
          report: {
            status: "failed",
            attempts: [{}, {}, {}],
            actual_execution_seconds: 1.25,
          },
        },
        {
          report: {
            status: "blocked",
            attempts: [],
            actual_execution_seconds: 0,
          },
        },
      ],
    };
    expect(summarizeDemo(payload)).toEqual({
      jobs: 2,
      attempts: 3,
      executionSeconds: 1.25,
      statuses: { failed: 1, blocked: 1 },
      complete: true,
    });
  });
  it("does not count a missing report as a successful zero-attempt job", () => {
    expect(
      summarizeDemo({
        results: [{ exit_code: 124, error: "Wrapper timed out" }],
      }),
    ).toEqual({
      jobs: 1,
      attempts: null,
      executionSeconds: null,
      statuses: { unknown: 1 },
      complete: false,
    });
    expect(summarizeDemo(null)).toBeNull();
    expect(summarizeDemo({ results: [] })).toBeNull();
  });
  it("keeps unknown timing unavailable without discarding known attempts", () => {
    expect(
      summarizeDemo({
        results: [
          {
            report: {
              status: "passed",
              attempts: [{}],
              actual_execution_seconds: -1,
            },
          },
        ],
      }),
    ).toMatchObject({ attempts: 1, executionSeconds: null, complete: false });
  });
});
