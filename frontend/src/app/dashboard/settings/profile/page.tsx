import { ProfilePanel } from "@/components/profile/profile-panel";

export default function SettingsProfilePage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold">Profile</h2>
        <p className="text-sm text-muted-foreground">
          Your professional profile powers job matching and cover letter generation.
        </p>
      </div>
      <ProfilePanel />
    </div>
  );
}
