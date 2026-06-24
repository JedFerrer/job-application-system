import { ApplicationsPanel } from "@/components/applications/applications-panel";

export default function ApplicationsPage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Applications</h1>
        <p className="text-sm text-muted-foreground">
          Track jobs you've saved and move them through your pipeline.
        </p>
      </header>
      <ApplicationsPanel />
    </div>
  );
}
