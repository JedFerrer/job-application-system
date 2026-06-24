import { JobsPanel } from "@/components/jobs/jobs-panel";

export default function JobsPage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
        <p className="text-sm text-muted-foreground">
          Ingest postings from job boards and browse what's in the feed.
        </p>
      </header>
      <JobsPanel />
    </div>
  );
}
