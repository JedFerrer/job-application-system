import Link from "next/link";
import { ResumePanel } from "@/components/resume/resume-panel";

export default function ResumePage() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tailored resumes</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Saved job-specific resume snapshots, ready to review and export.
          </p>
        </div>
        <Link
          href="/settings/profile"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
        >
          Manage profile
        </Link>
      </header>
      <div className="w-full max-w-5xl">
        <ResumePanel />
      </div>
    </div>
  );
}
