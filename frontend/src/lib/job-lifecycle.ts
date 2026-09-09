export type JobLifecycleFields = {
  status?: "active" | "expired" | "unknown" | "closed";
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  expired_at?: string | null;
  not_recently_seen?: boolean | null;
};

function formatLocalTimestamp(value: string | null | undefined): string {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

export function getJobLifecyclePresentation(job: JobLifecycleFields) {
  const status = job.status ?? "active";
  return {
    firstFound: formatLocalTimestamp(job.first_seen_at),
    lastObserved: formatLocalTimestamp(job.last_seen_at),
    statusLabel:
      status === "expired"
        ? "Outside search age window"
        : status === "closed"
          ? "Explicitly closed"
          : status === "unknown"
            ? "Lifecycle unknown"
            : job.not_recently_seen
              ? "Not recently seen"
              : null,
    closureLabel: status === "closed" ? "Explicitly closed" : "Not verified",
    isReadOnly: status !== "active",
  };
}
