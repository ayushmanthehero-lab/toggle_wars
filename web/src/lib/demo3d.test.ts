import { describe, it, expect } from "vitest";
// @ts-expect-error The static, dependency-free demo is also served directly by FastAPI.
import { frames, nextFrame, policies } from "../../public/demo-3d/story.mjs";
describe("3D simulation evidence", () => {
  it("keeps blocked distinct from pass, and the private dependency distinct from staging", () => {
    expect(frames.map((frame: { outcome: string }) => frame.outcome)).toEqual(["Passed","Failed","Blocked","Passed"]);
    expect(frames.map((frame: { attempts: number }) => frame.attempts)).toEqual([1,3,0,1]);
    expect(frames[3]).toMatchObject({ isolated: true, shared: false });
  });
  it("keeps the Flagsmith fixture separate from Parallax", () => {
    expect(policies.observe.attempts).toBe(12);
    expect(policies.enforce.attempts).toBe(6);
    expect(policies.enforce.rerunAttempts).toBe(0);
  });
  it("bounds next and back to the playable range", () => {
    expect(nextFrame(0,-1)).toBe(0);
    expect(nextFrame(1,1)).toBe(2);
    expect(nextFrame(3,1)).toBe(3);
  });
});
