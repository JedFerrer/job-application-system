import { ResumePanel } from "@/components/resume/resume-panel";

export default function SettingsProfilePage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold">Profile</h2>
        <p className="text-sm text-muted-foreground">
          Upload and review the profile that powers matching and cover letters.
        </p>
      </div>
      <ResumePanel />
    </div>
  );
}
