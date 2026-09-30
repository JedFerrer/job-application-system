"use client";

import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { applicationsApi, ApiError } from "@/lib/api";
import type { ApplicationInterviewPrepRequest, ApplicationInterviewPrepResponse, ApplicationInterviewStage, ApplicationSubmissionSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const material = { used_selected_package: "Selected package", used_other_material: "Other material", materials_unknown: "Unknown material" } as const;
const stages: ApplicationInterviewStage[] = ["screening", "technical", "behavioral", "final", "unspecified"];
export const interviewPrepKey = (id: number, context: string) => ["application-interview-prep", id, context] as const;

export function ApplicationInterviewPrep({ applicationId, submissions, loading, hasMore = false, onLoadMore }: { applicationId: number; submissions: ApplicationSubmissionSummary[]; loading: boolean; hasMore?: boolean; onLoadMore?: () => void }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const [chosenId, setChosenId] = useState<number | null>(null);
  const [explicitChoice, setExplicitChoice] = useState(false);
  const [fallback, setFallback] = useState(false);
  const [stage, setStage] = useState<ApplicationInterviewStage>("unspecified");
  const [focus, setFocus] = useState("");
  const [count, setCount] = useState(8);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApplicationInterviewPrepResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const selectedId = explicitChoice ? chosenId : submissions.length === 1 ? submissions[0]?.id ?? null : null;
  const selected = submissions.find((item) => item.id === selectedId);
  const historical = selected?.material_choice === "used_selected_package";
  const needsChoice = submissions.length > 1 && (!explicitChoice || chosenId === null);
  const canGenerate = !loading && !hasMore && !needsChoice && (historical || fallback) && count >= 3 && count <= 20;
  const areas = focus.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
  const generate = async (force: boolean) => {
    if (!canGenerate || areas.length > 10 || areas.some((item) => item.length > 200) || busy) return;
    const payload: ApplicationInterviewPrepRequest = { submission_id: selectedId, current_profile_fallback: !historical && fallback, stage, focus_areas: areas, question_count: count, force_refresh: force };
    setBusy(true);
    setErrorMessage(null);
    try {
      const data = await applicationsApi.prepareInterview(applicationId, payload);
      const key = interviewPrepKey(applicationId, JSON.stringify({ submission: selectedId, fallback: payload.current_profile_fallback, stage, areas, count }));
      client.setQueryData(key, data);
      setResult(data);
    } catch (error) {
      const message = error instanceof ApiError && error.status === 502 ? "Interview preparation is temporarily unavailable. Try again." : error instanceof ApiError && error.status === 422 ? "This interview context is unavailable. Review your selection and try again." : "Could not prepare interview questions. Try again.";
      setErrorMessage(message);
      toast.error(message);
    }
    finally { setBusy(false); }
  };
  const choose = (id: string) => { setExplicitChoice(true); setChosenId(id ? Number(id) : null); setFallback(false); setResult(null); };
  const sourceName = (source: string) => ({ job: "Job requirement", resume: "Resume", cover_letter: "Cover letter", manual_questions: "Preparation question", profile: "Current profile" }[source] ?? "Source");
  return <section className="min-w-0 rounded-lg border bg-card p-4"><h2 className="font-semibold">Interview preparation</h2><p className="mt-2 text-sm text-muted-foreground">Choose a confirmed historical submission or explicitly use your current profile as fallback.</p><div className="mt-4"><Button ref={trigger} size="sm" variant="outline" onClick={() => setOpen(true)}>Prepare for interview</Button></div>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto" onCloseAutoFocus={(event) => { if (trigger.current) { event.preventDefault(); trigger.current.focus(); } }}><DialogHeader><DialogTitle>Prepare for interview</DialogTitle><DialogDescription>Questions use the context selected here. Historical submitted material and current profile context are labeled separately.</DialogDescription></DialogHeader>
      {loading ? <p role="status">Loading submission choices…</p> : <div className="space-y-4">
        {submissions.length ? <div><label htmlFor="interview-submission" className="text-sm font-medium">Confirmed submission</label><select id="interview-submission" className="mt-1 w-full rounded border bg-background p-2 text-sm" value={selectedId ?? ""} onChange={(event) => choose(event.target.value)}><option value="">Choose a submission</option>{submissions.map((item) => <option key={item.id} value={item.id}>{new Date(item.submitted_at).toLocaleString()} · {material[item.material_choice]} · {item.package_id ? `Package #${item.package_id}, ${item.artifact_ids.length} files` : "No package"}</option>)}</select>{needsChoice ? <p className="mt-1 text-sm text-muted-foreground">Choose one historical record before generating questions.</p> : null}</div> : <p className="text-sm">Submitted material not recorded. Interview prep can use current profile context only after you opt in.</p>}
        {hasMore ? <div><p className="text-sm text-muted-foreground">Load the remaining history before choosing interview context.</p><Button size="sm" variant="outline" onClick={onLoadMore}>Load more submissions</Button></div> : null}
        {historical ? <p className="text-sm font-medium">Based on confirmed historical submission #{selectedId}</p> : <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={fallback} disabled={needsChoice} onChange={(event) => setFallback(event.target.checked)} /><span>Use current profile as fallback. This is current context, not confirmed submitted material.</span></label>}
        <div><label htmlFor="interview-stage" className="text-sm font-medium">Interview stage</label><select id="interview-stage" className="mt-1 w-full rounded border bg-background p-2 text-sm" value={stage} onChange={(event) => setStage(event.target.value as ApplicationInterviewStage)}>{stages.map((value) => <option key={value} value={value}>{value.charAt(0).toUpperCase() + value.slice(1)}</option>)}</select></div>
        <div><label htmlFor="interview-focus" className="text-sm font-medium">Focus areas (optional, comma separated, up to 10)</label><Input id="interview-focus" value={focus} onChange={(event) => setFocus(event.target.value)} /><p className="text-xs text-muted-foreground">Each area may be up to 200 characters.</p></div>
        <div><label htmlFor="interview-count" className="text-sm font-medium">Question count (3–20)</label><Input id="interview-count" type="number" min={3} max={20} value={count} onChange={(event) => setCount(Number(event.target.value))} /></div>
        {areas.length > 10 || areas.some((item) => item.length > 200) ? <p role="alert" className="text-sm">Use at most 10 focus areas of 200 characters each.</p> : null}
        <DialogFooter><Button disabled={!canGenerate || areas.length > 10 || areas.some((item) => item.length > 200)} aria-disabled={busy} onClick={() => void generate(false)}>{busy ? "Preparing…" : "Generate questions"}</Button></DialogFooter>
        {errorMessage ? <p role="alert" className="text-sm text-destructive">{errorMessage}</p> : null}
        {result ? <div className="min-w-0 space-y-4 border-t pt-4 text-sm [overflow-wrap:anywhere]" aria-label="Interview questions"><div><p className="font-medium">{result.source_label === "confirmed_submission" ? "Based on confirmed historical submission" : "Using current profile as fallback"}</p><p className="text-muted-foreground">{result.stage.charAt(0).toUpperCase() + result.stage.slice(1)} · {result.question_count} questions{result.focus_areas.length ? ` · Focus: ${result.focus_areas.join(", ")}` : ""}{result.cached ? " · Saved result" : ""}</p></div><ol className="list-inside list-decimal space-y-4">{result.questions.map((item, index) => <li key={index}><span className="font-medium">{item.question}</span><p className="mt-1 whitespace-pre-wrap">Suggested answer: {item.suggested_answer}</p><ul className="mt-1 text-xs text-muted-foreground">{item.source_locators.map((locator) => { const evidence = result.evidence.find((entry) => entry.locator === locator); return <li key={locator}>{evidence ? sourceName(evidence.source) : "Source"}: <span className="break-all">{locator}</span></li>; })}</ul></li>)}</ol><Button size="sm" variant="outline" disabled={!canGenerate} aria-disabled={busy} onClick={() => void generate(true)}>Regenerate questions</Button></div> : null}
      </div>}
    </DialogContent></Dialog>
  </section>;
}
