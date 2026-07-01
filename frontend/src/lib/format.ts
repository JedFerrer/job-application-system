/** Formatting helpers for job card display. */

/**
 * Compact salary display.
 *   formatSalary(80000, 120000, "USD") => "$80K–$120K"
 *   formatSalary(30, 80, "USD")        => "$30–$80/hr"
 *   formatSalary(null, null)           => ""
 */
export function formatSalary(
  min: number | null | undefined,
  max: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (min == null && max == null) return "";

  const fmt = (n: number): string => {
    if (n >= 10_000) {
      const k = n / 1000;
      return k % 1 === 0 ? `${k}K` : `${k.toFixed(0)}K`;
    }
    return n.toLocaleString();
  };

  const symbol = currency === "USD" ? "$" : currency ? `${currency} ` : "";

  // Heuristic: if max < 500, likely hourly
  const isHourly = max != null && max < 500;

  if (min != null && max != null) {
    return `${symbol}${fmt(min)}–${fmt(max)}${isHourly ? "/hr" : ""}`;
  }
  if (min != null) return `${symbol}${fmt(min)}${isHourly ? "/hr" : ""}`;
  return `${symbol}${fmt(max!)}${isHourly ? "/hr" : ""}`;
}

/**
 * Human-friendly relative date.
 *   formatRelativeDate("2026-06-30") => "today"
 *   formatRelativeDate("2026-06-28") => "2d ago"
 *   formatRelativeDate("2026-05-15") => "May 15"
 */
export function formatRelativeDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (isNaN(date.getTime())) return "";

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 0) return "just now";
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay === 1) return "yesterday";
  if (diffDay < 7) return `${diffDay}d ago`;
  if (diffDay < 30) return `${Math.floor(diffDay / 7)}w ago`;

  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
