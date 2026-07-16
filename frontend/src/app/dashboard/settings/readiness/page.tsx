import { ReadinessPreferencesForm } from "@/components/preferences/readiness-preferences-form";

export default function ReadinessSettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold">Readiness preferences</h2>
        <p className="text-sm text-muted-foreground">
          Set explicit rules that help explain whether a matched job is ready to
          pursue. These rules affect recommendations only. They do not change job
          search results.
        </p>
      </div>
      <ReadinessPreferencesForm />
    </div>
  );
}
