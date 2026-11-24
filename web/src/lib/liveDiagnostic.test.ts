import { expect, test } from "vitest";
import { liveDiagnosticWorkflow } from "./liveDiagnostic";

test("dashboard snippet is a manual protected-ref diagnostic, not a merge or release gate", () => {
  const yaml = liveDiagnosticWorkflow("inventory-api");
  expect(yaml).toContain("on: workflow_dispatch");
  expect(yaml).not.toMatch(/pull_request|merge_group|continue-on-error/);
  expect(yaml).toContain("contents: read");
  expect(yaml).toContain("github.ref_protected");
  expect(yaml).toContain("github.ref_type");
  expect(yaml).toContain("exit 1");
  expect(yaml.indexOf("github.ref_protected")).toBeLessThan(yaml.indexOf("actions/checkout"));
  expect(yaml.indexOf("github.ref_protected")).toBeLessThan(yaml.indexOf("secrets.CI_GUARD_PROJECT_TOKEN"));
  expect(yaml).toContain("persist-credentials: false");
  expect(yaml).toContain("dependency: \"inventory-api\"");
  expect(yaml).toContain("not a merge or release gate");
});

test("invalid dependency text cannot insert workflow syntax or an expression", () => {
  const yaml = liveDiagnosticWorkflow('inventory\n${{ secrets.OTHER }}');
  expect(yaml).toContain("dependency: \"<registered-dependency-id>\"");
  expect(yaml).not.toContain("secrets.OTHER");
});
