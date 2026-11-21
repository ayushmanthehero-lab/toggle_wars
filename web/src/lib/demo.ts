export type DemoSummary = {
  jobs: number;
  attempts: number | null;
  executionSeconds: number | null;
  statuses: Record<string, number>;
  complete: boolean;
};
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
export function summarizeDemo(payload: unknown): DemoSummary | null {
  const data = record(payload);
  if (!data || !Array.isArray(data.results) || data.results.length === 0)
    return null;
  let attempts = 0,
    seconds = 0,
    attemptsKnown = true,
    secondsKnown = true;
  const statuses: Record<string, number> = {};
  for (const entry of data.results) {
    const report = record(record(entry)?.report);
    const status =
      typeof report?.status === "string" &&
      ["passed", "failed", "blocked", "error", "cancelled"].includes(
        report.status,
      )
        ? report.status
        : "unknown";
    statuses[status] = (statuses[status] || 0) + 1;
    if (Array.isArray(report?.attempts)) attempts += report.attempts.length;
    else attemptsKnown = false;
    const duration = report?.actual_execution_seconds;
    if (
      typeof duration === "number" &&
      Number.isFinite(duration) &&
      duration >= 0
    )
      seconds += duration;
    else secondsKnown = false;
  }
  return {
    jobs: data.results.length,
    attempts: attemptsKnown ? attempts : null,
    executionSeconds: secondsKnown ? seconds : null,
    statuses,
    complete: attemptsKnown && secondsKnown && !statuses.unknown,
  };
}
