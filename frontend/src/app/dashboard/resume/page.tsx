import { ResumePanel } from "@/components/resume/resume-panel";

export default function ResumePage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Résumé</h1>
        <p className="text-sm text-muted-foreground">
          Upload and review the profile that powers matching and cover letters.
        </p>
      </header>
      <ResumePanel />
    </div>
  );
}
