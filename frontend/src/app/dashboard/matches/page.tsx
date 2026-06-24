import { MatchesPanel } from "@/components/matches/matches-panel";

export default function MatchesPage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Matches</h1>
        <p className="text-sm text-muted-foreground">
          Jobs ranked against your profile. Open one for a gap analysis or
          generate a cover letter.
        </p>
      </header>
      <MatchesPanel />
    </div>
  );
}
