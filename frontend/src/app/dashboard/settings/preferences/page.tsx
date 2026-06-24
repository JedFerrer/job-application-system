import { PreferencesForm } from "@/components/preferences/preferences-form";

export default function SettingsPreferencesPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold">Job preferences</h2>
        <p className="text-sm text-muted-foreground">
          Tune what the scheduled scraper looks for and trigger an on-demand
          refresh.
        </p>
      </div>
      <PreferencesForm />
    </div>
  );
}
