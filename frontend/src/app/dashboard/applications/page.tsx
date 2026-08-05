import Link from "next/link";
import { ApplicationsPanel } from "@/components/applications/applications-panel";

export default function ApplicationsPage() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Applications</h1>
        <div className="flex flex-col items-start gap-2 sm:items-end">
          <p className="text-sm text-muted-foreground">
            Track jobs you've saved and move them through your pipeline.
          </p>
          <Link
            href="/resume"
            className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          >
            Tailored resumes
          </Link>
        </div>
      </header>
      <ApplicationsPanel />
    </div>
  );
}
