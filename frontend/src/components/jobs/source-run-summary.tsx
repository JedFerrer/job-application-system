import type { SourceRunResult } from "@/lib/types";

const labels: Record<SourceRunResult["status"], string> = {
  queued: "Queued", running: "Running", success: "Completed", empty: "No results returned",
  unsupported: "Unsupported", unavailable: "Unavailable", failed: "Failed", skipped_fresh: "Already fresh",
};

export function SourceRunSummary({ results }: { results: SourceRunResult[] }) {
  if (results.length === 0) return null;
  return (
    <section aria-label="Source refresh summary" className="rounded-lg border bg-card p-4">
      <h2 className="text-sm font-medium">Source refresh summary</h2>
      <ul className="mt-3 space-y-2 text-sm">
        {results.map((result) => (
          <li key={result.source} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <span className="font-medium capitalize">{result.source.replace(/_/g, " ")}</span>
            <span className="text-muted-foreground">
              {labels[result.status]} · {result.fetched} returned, {result.accepted} accepted
              {result.rejected ? `, ${result.rejected} rejected` : ""}
              {result.completeness === "unknown" && result.status === "empty" ? " · completeness unknown" : ""}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
