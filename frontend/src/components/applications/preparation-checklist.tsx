import { CheckCircle2, CircleAlert, CircleDashed } from "lucide-react";
import type { ApplicationPreparation } from "@/lib/types";

export function PreparationChecklist({ preparation }: { preparation: ApplicationPreparation }) {
  const questionsRequired = preparation.manual_questions.length > 0;
  const completedQuestions = preparation.manual_questions.filter((question) => question.acknowledged).length;
  const checks = [
    { label: "Fit acknowledged", complete: preparation.fit_acknowledged, detail: preparation.fit_context_available ? null : "Match analysis is unavailable; acknowledgement is still available." },
    { label: "Tailored resume selected", complete: preparation.resume_current, detail: preparation.resume_draft_id && !preparation.resume_current ? "The selected resume has changed or is unavailable." : null },
    { label: "Cover letter decision", complete: preparation.cover_letter_mode === "not_needed" || (preparation.cover_letter_mode === "selected" && preparation.cover_letter_current), detail: preparation.cover_letter_mode === "unset" ? "Select a letter or mark it not needed." : preparation.cover_letter_mode === "selected" && !preparation.cover_letter_current ? "The selected cover letter has changed or is unavailable." : null },
    ...(questionsRequired ? [{ label: "Manual questions acknowledged", complete: preparation.manual_questions.every((question) => question.acknowledged), detail: `${completedQuestions} of ${preparation.manual_questions.length} complete. Review each answer after editing.` }] : []),
  ];
  const completed = checks.filter((check) => check.complete).length;
  return <section aria-labelledby="preparation-checklist-heading" className="rounded-lg border bg-card p-4">
    <div className="flex items-baseline justify-between gap-3"><div><h2 id="preparation-checklist-heading" className="font-semibold">Preparation checklist</h2><p className="text-sm text-muted-foreground">{preparation.state.replace("_", " ")}</p></div><p role="status" className="text-sm font-medium">{completed} of {checks.length} complete</p></div>
    <div className="mt-4 space-y-3">{checks.map((check) => <div key={check.label} className="flex gap-3 text-sm">
      {check.complete ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden /> : check.detail ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden /> : <CircleDashed className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />}
      <div><p className={check.complete ? "font-medium" : "font-medium text-muted-foreground"}>{check.label}</p>{check.detail ? <p className="text-muted-foreground">{check.detail}</p> : null}</div>
    </div>)}</div>
  </section>;
}
