"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowLeft, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi, coverLettersApi, resumeApi } from "@/lib/api";
import type { ApplicationPackage, ApplicationWorkspace, CoverLetter, ManualPreparationQuestion, TailoredResumeDraft } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { PackagePreview } from "./package-preview";
import { PreparationChecklist } from "./preparation-checklist";

function workspaceKey(id: number) { return ["application-workspace", id] as const; }
function uuid() { return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`; }

export function ApplicationWorkspace({ applicationId }: { applicationId: number }) {
  const client = useQueryClient();
  const [resumeOpen, setResumeOpen] = useState(false);
  const [letterOpen, setLetterOpen] = useState(false);
  const [resumeDraftId, setResumeDraftId] = useState<number | null>(null);
  const [resumeDirty, setResumeDirty] = useState(false);
  const [letterDirty, setLetterDirty] = useState(false);
  const [reviewPackage, setReviewPackage] = useState<ApplicationPackage | null>(null);
  const [selectedHistoricalPackageId, setSelectedHistoricalPackageId] = useState<number | null>(null);
  const [newQuestion, setNewQuestion] = useState("");
  const [newAnswer, setNewAnswer] = useState("");
  const [editingQuestionId, setEditingQuestionId] = useState<string | null>(null);
  const [editingQuestion, setEditingQuestion] = useState("");
  const [editingAnswer, setEditingAnswer] = useState("");
  const packageKey = useRef<string | null>(null);
  const editorOpenerRef = useRef<HTMLElement | null>(null);
  const workspace = useQuery({ queryKey: workspaceKey(applicationId), queryFn: () => applicationsApi.getWorkspace(applicationId) });
  const data = workspace.data;
  const savedResumes = useQuery({ queryKey: ["resume-drafts"], queryFn: resumeApi.listDrafts, retry: false });
  const savedResumeDetail = useQuery({ queryKey: ["resume-draft", resumeDraftId], queryFn: () => resumeApi.getDraft(resumeDraftId!), enabled: resumeDraftId != null, retry: false });
  const savedLetter = useQuery({ queryKey: ["cover-letter-by-job", data?.application.job_id], queryFn: () => coverLettersApi.getByJobId(data!.application.job_id), enabled: data != null, retry: false, staleTime: 0 });
  const historicalPackage = useQuery({
    queryKey: ["application-package", applicationId, selectedHistoricalPackageId],
    queryFn: () => applicationsApi.getPackage(applicationId, selectedHistoricalPackageId!),
    enabled: selectedHistoricalPackageId != null,
    retry: false,
  });
  const refresh = () => client.invalidateQueries({ queryKey: workspaceKey(applicationId) });
  const invalidateApplicationSurfaces = () => {
    void refresh();
    void client.invalidateQueries({ queryKey: ["applications"] });
    void client.invalidateQueries({ queryKey: ["dashboard-summary"] });
    void client.invalidateQueries({ queryKey: ["dashboard-action-center"] });
  };
  const conflict = () => { setReviewPackage(null); invalidateApplicationSurfaces(); toast.error("Preparation changed elsewhere. Review the refreshed details before continuing."); };
  const patch = useMutation({
    mutationFn: (payload: Parameters<typeof applicationsApi.patchPreparation>[1]) => applicationsApi.patchPreparation(applicationId, payload),
    onSuccess: invalidateApplicationSurfaces,
    onError: (error) => error instanceof ApiError && error.status === 409 ? conflict() : toast.error(error instanceof ApiError ? error.message : "Could not update preparation."),
  });
  const createPackage = useMutation({
    mutationFn: () => {
      if (!data) throw new Error("Workspace is not loaded");
      packageKey.current ??= uuid();
      return applicationsApi.createPackage(applicationId, data.preparation.version, packageKey.current);
    },
    onSuccess: (item) => { packageKey.current = null; setReviewPackage(item); invalidateApplicationSurfaces(); },
    onError: (error) => error instanceof ApiError && error.status === 409 ? conflict() : toast.error(error instanceof ApiError ? error.message : "Could not create the package."),
  });
  const approve = useMutation({
    mutationFn: () => {
      if (!data || !reviewPackage) throw new Error("No package is being reviewed");
      return applicationsApi.approvePreparation(applicationId, data.preparation.version, reviewPackage.id, reviewPackage.content_sha256);
    },
    onSuccess: () => { setReviewPackage(null); invalidateApplicationSurfaces(); toast.success("Package approved"); },
    onError: (error) => error instanceof ApiError && error.status === 409 ? conflict() : toast.error(error instanceof ApiError ? error.message : "Could not approve this package."),
  });
  const selectResume = (saved: Pick<TailoredResumeDraft, "id">) => data && patch.mutate({ expected_version: data.preparation.version, resume_draft_id: saved.id });
  const selectLetter = (saved: CoverLetter) => data && patch.mutate({ expected_version: data.preparation.version, cover_letter_mode: "selected", cover_letter_id: saved.id });
  const review = () => {
    if (!data) return;
    const canReviewPackage = data.job.open_job_available && (data.preparation.state === "needs_review" || data.preparation.state === "ready");
    if (!canReviewPackage) return;
    if (resumeDirty || letterDirty) { toast.error("Save or discard unsaved document edits before reviewing the package."); return; }
    createPackage.mutate();
  };
  const saveQuestions = (questions: ManualPreparationQuestion[], onSuccess?: () => void) => {
    if (!data) return;
    patch.mutate(
      { expected_version: data.preparation.version, manual_questions: questions },
      { onSuccess },
    );
  };
  const addQuestion = () => {
    if (!data || !newQuestion.trim()) return;
    saveQuestions(
      [...data.preparation.manual_questions, { question: newQuestion, answer: newAnswer, acknowledged: false }],
      () => { setNewQuestion(""); setNewAnswer(""); },
    );
  };
  const beginEditQuestion = (item: ManualPreparationQuestion) => {
    if (!item.id) return;
    setEditingQuestionId(item.id);
    setEditingQuestion(item.question);
    setEditingAnswer(item.answer);
  };
  const saveEditedQuestion = () => {
    if (!data || !editingQuestionId || !editingQuestion.trim()) return;
    saveQuestions(
      data.preparation.manual_questions.map((item) => item.id === editingQuestionId
        ? { ...item, question: editingQuestion, answer: editingAnswer }
        : item),
      () => { setEditingQuestionId(null); setEditingQuestion(""); setEditingAnswer(""); },
    );
  };
  const markQuestionComplete = (id: string) => data && saveQuestions(
    data.preparation.manual_questions.map((item) => item.id === id ? { ...item, acknowledged: true } : item),
  );
  const removeQuestion = (id: string) => data && saveQuestions(
    data.preparation.manual_questions.filter((item) => item.id !== id),
  );
  if (workspace.isLoading) return <div className="flex min-h-64 items-center justify-center" role="status"><Loader2 className="size-6 animate-spin" /> <span className="sr-only">Loading application workspace</span></div>;
  if (workspace.isError) {
    const notFound = workspace.error instanceof ApiError && workspace.error.status === 404;
    return <section className="rounded-lg border p-8 text-center"><h1 className="text-lg font-semibold">{notFound ? "Application not found" : "Could not load this workspace"}</h1><p className="mt-2 text-sm text-muted-foreground">{notFound ? "It may no longer be available." : "Try again in a moment."}</p>{!notFound && <Button className="mt-4" onClick={() => workspace.refetch()}><RefreshCw />Retry</Button>}</section>;
  }
  if (!data) return null;
  const { application, job, preparation } = data;
  const savedResume = savedResumes.data?.find((draft) => draft.job_id === application.job_id) ?? null;
  const savedLetterMissing = savedLetter.error instanceof ApiError && savedLetter.error.status === 404;
  const savedLetterError = savedLetter.isError && !savedLetterMissing;
  const selectedResumeStale = preparation.resume_draft_id != null && !preparation.resume_current;
  const selectedLetterStale = preparation.cover_letter_mode === "selected" && !preparation.cover_letter_current;
  const canReviewPackage = job.open_job_available && (preparation.state === "needs_review" || preparation.state === "ready");
  const rememberEditorOpener = (target: EventTarget | null) => { if (target instanceof HTMLElement) editorOpenerRef.current = target; };
  const openSavedResume = (target: EventTarget | null) => { rememberEditorOpener(target); if (savedResume) setResumeDraftId(savedResume.id); };
  return <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 pb-24">
    <header className="rounded-lg border bg-card p-5"><Link href="/applications" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" />Applications</Link><div className="mt-3 flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-2xl font-semibold">{job.title}</h1><p className="text-muted-foreground">{[job.company, job.location].filter(Boolean).join(" · ") || "Historical application"}</p><p className="mt-2 text-sm">Outcome: <span className="font-medium capitalize">{application.status}</span></p></div>{job.open_job_available && job.source_url ? <Button asChild variant="outline"><a href={job.source_url} target="_blank" rel="noreferrer">Open job <ExternalLink /></a></Button> : <p className="max-w-sm text-sm text-muted-foreground">Current job context is unavailable. Your owned preparation and package history remain available.</p>}</div></header>
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(340px,0.8fr)]"><main className="space-y-6">
      <PreparationChecklist preparation={preparation} />
      <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Fit review</h2>{data.match_summary ? <p className="mt-2 text-sm text-muted-foreground">Matched: {data.match_summary.matched_skills.join(", ") || "No skills recorded"}</p> : <p className="mt-2 text-sm text-muted-foreground">No saved match analysis is available.</p>}<Button className="mt-3" size="sm" variant={preparation.fit_acknowledged ? "outline" : "default"} disabled={patch.isPending} onClick={() => patch.mutate({ expected_version: preparation.version, fit_acknowledged: !preparation.fit_acknowledged })}>{preparation.fit_acknowledged ? "Fit acknowledged" : "Acknowledge fit context"}</Button></section>
      <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Resume</h2><p className="mt-1 text-sm text-muted-foreground">{selectedResumeStale ? "The selected resume has changed or is unavailable. Re-select a saved resume before reviewing." : data.selected_resume ? `${data.selected_resume.label ?? "Saved tailored resume"} is selected.` : savedResume ? `Saved ${new Date(savedResume.updated_at).toLocaleDateString()}. Select it to use it for this application.` : "No saved tailored resume for this job."}</p><div className="mt-3 flex flex-wrap gap-2">{savedResume ? <><Button size="sm" disabled={patch.isPending} onClick={() => selectResume(savedResume)}>{selectedResumeStale ? "Re-select saved resume" : preparation.resume_draft_id ? "Select saved resume" : "Select saved resume"}</Button><Button size="sm" variant="outline" onClick={(event) => openSavedResume(event.currentTarget)}>View/edit saved resume</Button></> : <Button size="sm" onClick={(event) => { rememberEditorOpener(event.currentTarget); setResumeDraftId(null); setResumeOpen(true); }}>Tailor new resume</Button>}</div></section>
      <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Cover letter</h2><p className="mt-1 text-sm text-muted-foreground">{preparation.cover_letter_mode === "not_needed" ? "Not needed for this application." : selectedLetterStale ? "The selected cover letter has changed or is unavailable. Re-select a saved letter before reviewing." : data.selected_cover_letter ? `${data.selected_cover_letter.is_edited ? "Edited " : ""}saved letter selected.` : savedLetter.data ? `Saved letter updated ${new Date(savedLetter.data.updated_at).toLocaleDateString()}. Select it to use it for this application.` : savedLetterError ? "Could not load saved cover letter." : "No saved cover letter for this job."}</p><div className="mt-3 flex flex-wrap gap-2">{savedLetter.data ? <><Button size="sm" disabled={patch.isPending} onClick={() => selectLetter(savedLetter.data!)}>{selectedLetterStale ? "Re-select saved letter" : "Select saved letter"}</Button><Button size="sm" variant="outline" onClick={(event) => { rememberEditorOpener(event.currentTarget); setLetterOpen(true); }}>Edit saved letter</Button></> : <Button size="sm" onClick={(event) => { rememberEditorOpener(event.currentTarget); setLetterOpen(true); }}>Create cover letter</Button>}<Button size="sm" variant="outline" disabled={patch.isPending} onClick={() => patch.mutate({ expected_version: preparation.version, cover_letter_mode: "not_needed", cover_letter_id: null })}>No cover letter needed</Button></div></section>
      <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Manual questions</h2><p className="mt-1 text-sm text-muted-foreground">Optional. Add only questions you want to track, then explicitly mark each one complete.</p>
        {preparation.manual_questions.length ? <div className="mt-4 space-y-3">{preparation.manual_questions.map((item, index) => <div key={item.id ?? `${item.question}-${index}`} className="rounded-md border p-3">
          {editingQuestionId === item.id ? <div className="space-y-3"><div><label className="text-sm font-medium" htmlFor={`manual-question-${item.id}`}>Question</label><Textarea id={`manual-question-${item.id}`} className="mt-1" value={editingQuestion} maxLength={500} onChange={(event) => setEditingQuestion(event.target.value)} /></div><div><label className="text-sm font-medium" htmlFor={`manual-answer-${item.id}`}>Answer</label><Textarea id={`manual-answer-${item.id}`} className="mt-1" value={editingAnswer} maxLength={4000} onChange={(event) => setEditingAnswer(event.target.value)} /></div><div className="flex flex-wrap gap-2"><Button size="sm" onClick={saveEditedQuestion} disabled={patch.isPending || !editingQuestion.trim()}>Save changes</Button><Button size="sm" variant="outline" onClick={() => { setEditingQuestionId(null); setEditingQuestion(""); setEditingAnswer(""); }} disabled={patch.isPending}>Cancel</Button></div></div> : <><p className="font-medium">{item.question}</p><p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">{item.answer || "No answer recorded."}</p><p className="mt-2 text-sm" role="status">{item.acknowledged ? "Complete" : "Incomplete"}</p><div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => beginEditQuestion(item)} disabled={patch.isPending || !item.id}>Edit</Button>{!item.acknowledged && item.id ? <Button size="sm" onClick={() => { if (item.id) markQuestionComplete(item.id); }} disabled={patch.isPending}>Mark complete</Button> : null}{item.id ? <Button size="sm" variant="outline" onClick={() => { if (item.id) removeQuestion(item.id); }} disabled={patch.isPending}>Remove</Button> : null}</div></>}
        </div>)}</div> : null}
        <div className="mt-4 space-y-3 border-t pt-4"><div><label className="text-sm font-medium" htmlFor="new-manual-question">Question</label><Textarea id="new-manual-question" className="mt-1" value={newQuestion} maxLength={500} onChange={(event) => setNewQuestion(event.target.value)} placeholder="Optional question to track" /></div><div><label className="text-sm font-medium" htmlFor="new-manual-answer">Answer</label><Textarea id="new-manual-answer" className="mt-1" value={newAnswer} maxLength={4000} onChange={(event) => setNewAnswer(event.target.value)} placeholder="Optional answer" /></div><Button size="sm" variant="outline" onClick={addQuestion} disabled={patch.isPending || !newQuestion.trim() || preparation.manual_questions.length >= 20}>Add question</Button></div>
      </section>
      <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Next action</h2><p className="mt-2 text-sm text-muted-foreground">{application.next_action_type ? `${application.next_action_type.replace("_", " ")}${application.next_action_due_date ? ` · due ${application.next_action_due_date}` : ""}` : "No next action set."}</p></section>
      <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Activity</h2>{data.activity.events.length ? <ul className="mt-2 space-y-2 text-sm">{data.activity.events.map((event) => <li key={event.id}>{event.event_type.replaceAll("_", " ")} · {new Date(event.created_at).toLocaleString()}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">No activity yet.</p>}</section>
    </main><aside className="space-y-6">
      {selectedHistoricalPackageId != null ? <section className="rounded-lg border bg-card p-4"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium">Viewing historical package revision {data.package_history.find((item) => item.id === selectedHistoricalPackageId)?.revision ?? ""}</p><Button size="sm" variant="outline" onClick={() => setSelectedHistoricalPackageId(null)}>Back to current package</Button></div><div className="mt-4">{historicalPackage.isLoading ? <p className="text-sm text-muted-foreground" role="status">Loading package revision…</p> : historicalPackage.isError ? <p className="text-sm text-muted-foreground" role="alert">{historicalPackage.error instanceof ApiError && historicalPackage.error.status === 404 ? "Package revision is unavailable." : "Could not load this package revision."}</p> : historicalPackage.data ? <PackagePreview package={historicalPackage.data} /> : null}</div></section> : reviewPackage ?? data.approved_package ? <PackagePreview package={reviewPackage ?? data.approved_package!} /> : <section className="rounded-lg border p-4 text-sm text-muted-foreground">No approved package yet.</section>}
      <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Package history</h2>{data.package_history.length ? <ul className="mt-2 space-y-2 text-sm">{data.package_history.map((item) => <li key={item.id} className="flex flex-wrap items-center justify-between gap-2"><span>Revision {item.revision}{item.is_approved ? " · approved" : ""} · {new Date(item.created_at).toLocaleString()}</span><Button size="sm" variant="outline" onClick={() => setSelectedHistoricalPackageId(item.id)}>View</Button></li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">No package revisions yet.</p>}</section>
    </aside></div>
    <div className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/95 p-3 backdrop-blur"><div className="mx-auto flex max-w-7xl flex-col items-end gap-2 sm:flex-row sm:justify-end"><p id="review-package-requirements" className="text-right text-sm text-muted-foreground">{canReviewPackage ? "Review the current package before approval." : "Complete the preparation checklist before reviewing your package."}</p><Button onClick={review} disabled={!canReviewPackage || createPackage.isPending} aria-describedby="review-package-requirements">{createPackage.isPending ? <Loader2 className="animate-spin" /> : null}Review package</Button>{reviewPackage ? <Button onClick={() => approve.mutate()} disabled={approve.isPending}>{approve.isPending ? <Loader2 className="animate-spin" /> : null}Approve package</Button> : null}</div></div>
    {resumeOpen && resumeDraftId == null ? <TailorResumeDialog jobId={application.job_id} jobTitle={job.title} open onOpenChange={setResumeOpen} onDirtyChange={setResumeDirty} onSaved={() => { void savedResumes.refetch(); void refresh(); }} returnFocusRef={editorOpenerRef} /> : null}
    {resumeDraftId != null && savedResumeDetail.data ? <TailorResumeDialog jobId={application.job_id} jobTitle={job.title} open onOpenChange={(open) => { if (!open) { setResumeOpen(false); setResumeDraftId(null); } }} initialData={savedResumeDetail.data.content} onDirtyChange={setResumeDirty} onSaved={() => { void savedResumes.refetch(); void refresh(); }} returnFocusRef={editorOpenerRef} /> : null}
    <CoverLetterDialog jobId={application.job_id} jobTitle={job.title} open={letterOpen} onOpenChange={setLetterOpen} onDirtyChange={setLetterDirty} onSaved={() => { void savedLetter.refetch(); void refresh(); }} returnFocusRef={editorOpenerRef} />
  </div>;
}
