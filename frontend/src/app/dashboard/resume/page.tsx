import { ResumePanel } from "@/components/resume/resume-panel";

export default function ResumePage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Resume</h1>
        <p className="text-sm text-muted-foreground">
          Upload your resume, tailor it to jobs, and manage saved drafts.
        </p>
      </header>
      <ResumePanel />
    </div>
  );
}
