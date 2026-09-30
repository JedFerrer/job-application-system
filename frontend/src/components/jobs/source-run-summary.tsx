import { useState } from "react";
import { X } from "lucide-react";
import type { SourceRunResult } from "@/lib/types";

const labels: Record<SourceRunResult["status"], string> = {
  queued: "Queued",
  running: "Running",
  success: "Completed",
  empty: "No results returned",
  unsupported: "Unsupported",
  unavailable: "Unavailable",
  failed: "Failed",
  skipped_fresh: "Already fresh",
};

const sourceLabels: Record<string, string> = {
  indeed: "Indeed",
  linkedin: "LinkedIn",
  glassdoor: "Glassdoor",
  google: "Google Jobs",
  zip_recruiter: "ZipRecruiter",
};

function sourceLabel(source: string) {
  return sourceLabels[source] ?? source.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function SourceRunSummary({ results }: { results: SourceRunResult[] }) {
  const [isDismissed, setIsDismissed] = useState(false);
  if (results.length === 0 || isDismissed) return null;

  return (
    <section aria-labelledby="source-refresh-heading" className="rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 id="source-refresh-heading" className="text-sm font-semibold">Refresh results</h2>
        <div className="flex items-center gap-2">
          <p className="text-xs text-muted-foreground">
            {results.length} {results.length === 1 ? "source result" : "source results"}
          </p>
          <button
            type="button"
            aria-label="Hide refresh results"
            title="Hide refresh results"
            onClick={() => setIsDismissed(true)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      </div>
      <p role="status" className="sr-only">
        {results.map((result) => `${sourceLabel(result.source)}: ${labels[result.status]}`).join("; ")}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Accepted postings include both new jobs and updates to existing jobs.
      </p>
      <ul className="mt-4 grid gap-3 md:grid-cols-2">
        {results.map((result) => {
          const showCounts = result.status === "success" || result.status === "empty";

          return (
            <li key={result.source} className="min-w-0 rounded-md border bg-background/50 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="min-w-0 text-sm font-medium">{sourceLabel(result.source)}</h3>
                <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                  result.status === "success"
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
                    : result.status === "failed" || result.status === "unavailable" || result.status === "unsupported"
                      ? "border-amber-500/30 bg-amber-500/10 text-amber-500"
                      : "border-border bg-muted text-muted-foreground"
                }`}>
                  {labels[result.status]}
                </span>
              </div>
              {showCounts && (
                <>
                  <div className="mt-3 grid grid-cols-2 gap-3 border-t pt-3">
                    <div>
                      <p className="text-xs text-muted-foreground">Returned</p>
                      <p className="text-lg font-semibold tabular-nums">{result.fetched}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Accepted</p>
                      <p className="text-lg font-semibold tabular-nums">{result.accepted}</p>
                    </div>
                  </div>
                  {result.status === "success" && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {result.inserted} new · {result.updated} updated
                    </p>
                  )}
                  {result.rejected > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">{result.rejected} rejected</p>
                  )}
                </>
              )}
              {result.truncated && (
                <p className="mt-2 text-xs text-amber-500">Results may be incomplete.</p>
              )}
              {result.completeness === "unknown" && result.status === "empty" && (
                <p className="mt-2 text-xs text-muted-foreground">Source completeness unknown.</p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
