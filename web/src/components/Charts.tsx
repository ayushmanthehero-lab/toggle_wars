import { useId } from "react";

export function TrendChart({
  points,
}: {
  points: { date: string; attempts: number; prevented: number }[];
}) {
  const gradientId = useId().replace(/:/g, "");
  const data = points.slice(-14);
  if (!data.length || !data.some((d) => d.attempts || d.prevented))
    return (
      <div className="chart-empty">
        <div className="empty-chart-lines" />
        <span>No attempt history yet</span>
        <small>Run a protected suite to start your timeline.</small>
      </div>
    );
  const maximum = Math.max(
    ...data.map((d) => Math.max(d.attempts, d.prevented)),
    1,
  );
  const coords = (field: "attempts" | "prevented") =>
    data.map(
      (p, i) =>
        `${48 + (i / Math.max(data.length - 1, 1)) * 596},${180 - (p[field] / maximum) * 148}`,
    );
  const attemptLine = coords("attempts");
  const preventLine = coords("prevented");
  return (
    <div className="trend-chart">
      <svg
        viewBox="0 0 680 230"
        role="img"
        aria-label={`Daily recorded attempts and prevented attempts. ${data.map((p) => `${p.date}: ${p.attempts} attempts, ${p.prevented} prevented`).join(". ")}`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2478f5" stopOpacity=".13" />
            <stop offset="100%" stopColor="#2478f5" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line
              x1="48"
              x2="644"
              y1={180 - f * 148}
              y2={180 - f * 148}
              stroke="#e9edef"
              strokeDasharray="3 6"
            />
            <text
              x="32"
              y={184 - f * 148}
              textAnchor="end"
              fill="#929b9e"
              fontSize="11"
            >
              {Math.round(maximum * f)}
            </text>
          </g>
        ))}
        <path
          d={`M${attemptLine.join(" L")} L${data.length > 1 ? "644" : "48"},180 L48,180 Z`}
          fill={`url(#${gradientId})`}
        />
        <polyline
          points={attemptLine.join(" ")}
          fill="none"
          stroke="#2478f5"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <polyline
          points={preventLine.join(" ")}
          fill="none"
          stroke="#ed4f9b"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {data.map((p, i) => (
          <g key={p.date}>
            <circle
              cx={48 + (i / Math.max(data.length - 1, 1)) * 596}
              cy={180 - (p.attempts / maximum) * 148}
              r="3.5"
              fill="#2478f5"
            />
            <circle
              cx={48 + (i / Math.max(data.length - 1, 1)) * 596}
              cy={180 - (p.prevented / maximum) * 148}
              r="3.5"
              fill="#ed4f9b"
            />
            {(data.length < 8 || i % 2 === 0) && (
              <text
                x={48 + (i / Math.max(data.length - 1, 1)) * 596}
                y="215"
                textAnchor="middle"
                fill="#8c9698"
                fontSize="11"
              >
                {p.date.slice(5)}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

export function OutcomeChart({
  passed,
  failed,
  blocked,
  total,
}: {
  passed: number;
  failed: number;
  blocked: number;
  total: number;
}) {
  const radius = 62,
    circumference = 2 * Math.PI * radius;
  const other = Math.max(total - passed - failed - blocked, 0);
  const slices = [
    { value: passed, color: "#15b85c" },
    { value: failed, color: "#f2509b" },
    { value: blocked, color: "#287af4" },
    { value: other, color: "#d8dee1" },
  ];
  let offset = 0;
  return (
    <div className="outcome-content">
      <div className="donut">
        <svg
          viewBox="0 0 160 160"
          role="img"
          aria-label={`${passed} passed, ${failed} failed, ${blocked} blocked, ${other} other runs`}
        >
          <circle
            cx="80"
            cy="80"
            r={radius}
            fill="none"
            stroke="#f1f4f5"
            strokeWidth="12"
          />
          {slices
            .filter((s) => s.value > 0)
            .map((s, i) => {
              const length = (s.value / Math.max(total, 1)) * circumference;
              const start = offset;
              offset += length;
              return (
                <circle
                  key={i}
                  cx="80"
                  cy="80"
                  r={radius}
                  fill="none"
                  stroke={s.color}
                  strokeWidth="12"
                  strokeDasharray={`${Math.max(length - 5, 1)} ${circumference}`}
                  strokeDashoffset={-start}
                  transform="rotate(-90 80 80)"
                  strokeLinecap="round"
                />
              );
            })}
        </svg>
        <div className="donut-value">
          <strong>{total.toLocaleString()}</strong>
          <span>total runs</span>
        </div>
      </div>
      <div className="outcome-legend">
        {[
          { label: "Passed", value: passed, color: "green" },
          { label: "Failed", value: failed, color: "pink" },
          { label: "Blocked", value: blocked, color: "blue" },
          ...(other ? [{ label: "Other", value: other, color: "gray" }] : []),
        ].map((s) => (
          <div key={s.label}>
            <span>
              <i className={`dot ${s.color}`} />
              {s.label}
            </span>
            <strong>
              {s.value.toLocaleString()}
              <small>{total ? Math.round((s.value / total) * 100) : 0}%</small>
            </strong>
          </div>
        ))}
      </div>
    </div>
  );
}
