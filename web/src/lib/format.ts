type NumberValue = number | null | undefined;
type DateValue = string | number | Date | null | undefined;
export type StatusTone = "success" | "danger" | "warning" | "info" | "neutral";

const missing = "—";

function finiteNumber(value: NumberValue): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function validDate(value: DateValue): Date | null {
  if (value === undefined || value === null || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function formatNumber(value: NumberValue): string {
  return finiteNumber(value)
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value)
    : missing;
}

export function formatDuration(value: NumberValue): string {
  if (!finiteNumber(value) || value < 0) return missing;
  if (value === 0) return "0s";
  if (value < 0.001) return "<1ms";
  if (value < 1) return `${Math.floor(value * 1000)}ms`;
  if (value < 60) return `${Math.floor(value * 10) / 10}s`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes}m ${Math.floor(value % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatDate(value: DateValue): string {
  const date = validDate(value);
  return date
    ? new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      }).format(date)
    : missing;
}

export function formatTime(value: DateValue): string {
  const date = validDate(value);
  return date
    ? `${new Intl.DateTimeFormat("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
        timeZone: "UTC",
      }).format(date)} UTC`
    : missing;
}

export function relativeTime(value: DateValue, now = Date.now()): string {
  const date = validDate(value);
  if (!date || !Number.isFinite(now)) return missing;
  const delta = date.getTime() - now;
  const seconds = Math.floor(Math.abs(delta) / 1000);
  if (seconds < 5) return "just now";
  let label: string;
  if (seconds < 60) label = `${seconds}s`;
  else if (seconds < 3600) label = `${Math.floor(seconds / 60)}m`;
  else if (seconds < 86400) label = `${Math.floor(seconds / 3600)}h`;
  else label = `${Math.floor(seconds / 86400)}d`;
  return delta > 0 ? `in ${label}` : `${label} ago`;
}

export function statusLabel(value: string | null | undefined): string {
  if (!value?.trim()) return "Unknown";
  const label = value.trim().replace(/[_-]+/g, " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function statusTone(value: string | null | undefined): StatusTone {
  switch (value) {
    case "passed":
    case "healthy":
      return "success";
    case "failed":
    case "error":
    case "unhealthy":
    case "assertion":
    case "timeout":
      return "danger";
    case "blocked":
    case "block":
    case "would_block":
    case "observe":
      return "warning";
    case "running":
    case "queued":
    case "run":
    case "retry":
    case "probe":
      return "info";
    default:
      return "neutral";
  }
}
