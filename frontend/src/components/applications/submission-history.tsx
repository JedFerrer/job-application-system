"use client";

import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, applicationsApi } from "@/lib/api";
import type { ApplicationSubmissionConfirmation, ApplicationSubmissionSummary, ApplicationWorkspace, SubmissionMaterialChoice } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";

const labels: Record<SubmissionMaterialChoice, string> = {
  used_selected_package: "Used selected package", used_other_material: "Used other material", materials_unknown: "Materials unknown",
};
export const submissionListKey = (id: number) => ["application-submissions", id] as const;
export const submissionDetailKey = (id: number, submissionId: number) => ["application-submission", id, submissionId] as const;
const date = (value: string) => new Date(value).toLocaleString();
const newKey = () => crypto.randomUUID();
const submittedLabel = (reported: boolean) => reported ? "User-reported submitted time" : "Submitted time";

export function SubmissionHistory({ applicationId, workspace, onViewPackage, onPackageNavigationClosed }: { applicationId: number; workspace: ApplicationWorkspace; onViewPackage: (id: number) => void; onPackageNavigationClosed?: () => void }) {
  const client = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const confirmTrigger = useRef<HTMLButtonElement>(null);
  const navigatingToPackage = useRef(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const deleteTrigger = useRef<HTMLButtonElement>(null);
  const detailOpener = useRef<HTMLButtonElement | null>(null);
  const historyHeading = useRef<HTMLHeadingElement>(null);
  const loadMoreButton = useRef<HTMLButtonElement>(null);
  const detailButtons = useRef(new Map<number, HTMLButtonElement>());
  const pendingPagination = useRef<{ pageCount: number; existingIds: Set<number> } | null>(null);
  const deletionCompleted = useRef(false);
  const [choice, setChoice] = useState<SubmissionMaterialChoice>("materials_unknown");
  const [packageId, setPackageId] = useState("");
  const [artifactIds, setArtifactIds] = useState<number[]>([]);
  const [submittedAt, setSubmittedAt] = useState("");
  const [note, setNote] = useState("");
  const retry = useRef<{ signature: string; key: string } | null>(null);
  const submissions = useInfiniteQuery({
    queryKey: submissionListKey(applicationId), initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => applicationsApi.listSubmissions(applicationId, { cursor: pageParam ?? undefined, limit: 25 }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
  });
  const detail = useQuery({ queryKey: submissionDetailKey(applicationId, selectedId ?? 0), queryFn: () => applicationsApi.getSubmission(applicationId, selectedId!), enabled: selectedId !== null, retry: false });
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: submissionListKey(applicationId) }),
      client.invalidateQueries({ queryKey: ["application-submission", applicationId] }),
      client.invalidateQueries({ queryKey: ["application-workspace", applicationId] }),
      client.invalidateQueries({ queryKey: ["application-timeline", applicationId] }),
      client.invalidateQueries({ queryKey: ["applications"] }),
      client.invalidateQueries({ queryKey: ["dashboard-summary"] }),
      client.invalidateQueries({ queryKey: ["dashboard-action-center"] }),
      client.invalidateQueries({ queryKey: ["application-artifact", applicationId] }),
    ]);
  };
  const confirm = useMutation({
    mutationFn: ({ payload, key }: { payload: ApplicationSubmissionConfirmation; key: string }) => applicationsApi.confirmSubmission(applicationId, payload, key),
    onSuccess: async (row) => { retry.current = null; detailOpener.current = null; deletionCompleted.current = false; setConfirmOpen(false); setSelectedId(row.id); await refresh(); toast.success("Submitted material recorded"); },
    onError: (error) => toast.error(error instanceof ApiError ? error.status === 409 ? "This confirmation conflicts with existing history. Review it before trying again." : error.status === 422 ? "Check the submitted material, time, and note, then retry." : error.message : "Could not confirm materials. Check your connection and retry."),
  });
  const remove = useMutation({
    mutationFn: (id: number) => applicationsApi.deleteSubmission(applicationId, id),
    onSuccess: async () => { deletionCompleted.current = true; setDeleteOpen(false); if (selectedId !== null) client.removeQueries({ queryKey: submissionDetailKey(applicationId, selectedId) }); setSelectedId(null); await refresh(); toast.success("History details deleted"); },
    onError: async (error) => { if (error instanceof ApiError && error.status === 404) { deletionCompleted.current = true; setDeleteOpen(false); setSelectedId(null); await refresh(); toast.error("This history record is no longer available."); } else toast.error(error instanceof ApiError ? error.message : "Could not delete history."); },
  });
  const history = Array.from(new Map((submissions.data?.pages.flatMap((page) => page.items) ?? []).map((item) => [item.id, item])).values());
  useEffect(() => {
    const pending = pendingPagination.current;
    if (!pending || submissions.isFetchingNextPage) return;
    const pageLoaded = (submissions.data?.pages.length ?? 0) > pending.pageCount;
    if (!pageLoaded && !submissions.isFetchNextPageError) return;
    pendingPagination.current = null;
    if (submissions.isFetchNextPageError || submissions.hasNextPage) {
      loadMoreButton.current?.focus();
      return;
    }
    const firstNew = history.find((item) => !pending.existingIds.has(item.id));
    (firstNew && detailButtons.current.get(firstNew.id) || historyHeading.current)?.focus();
  }, [history, submissions.data, submissions.hasNextPage, submissions.isFetchNextPageError, submissions.isFetchingNextPage]);
  const loadMore = () => {
    if (submissions.isFetchingNextPage || pendingPagination.current || !submissions.hasNextPage) return;
    pendingPagination.current = { pageCount: submissions.data?.pages.length ?? 0, existingIds: new Set(history.map((item) => item.id)) };
    void submissions.fetchNextPage();
  };
  const packages = workspace.package_history;
  const selectedPackage = packages.find((item) => item.id === Number(packageId));
  const submit = () => {
    const payload: ApplicationSubmissionConfirmation = {
      material_choice: choice,
      ...(choice === "used_selected_package" ? { package_id: Number(packageId), artifact_ids: artifactIds } : {}),
      ...(submittedAt ? { submitted_at: new Date(submittedAt).toISOString() } : {}),
      note: note.trim() || null,
    };
    if (choice === "used_selected_package" && !selectedPackage) return;
    const signature = JSON.stringify(payload);
    if (!retry.current || retry.current.signature !== signature) retry.current = { signature, key: newKey() };
    confirm.mutate({ payload, key: retry.current.key });
  };
  const openConfirm = () => { retry.current = null; setChoice("materials_unknown"); setPackageId(""); setArtifactIds([]); setSubmittedAt(""); setNote(""); setConfirmOpen(true); };
  const row = detail.data;
  return <section className="min-w-0 rounded-lg border bg-card p-4" aria-label="Submission history">
    <h2 ref={historyHeading} tabIndex={-1} className="font-semibold">Submission history</h2>
    <p className="mt-2 text-sm text-muted-foreground">Record what you actually sent. Current documents are not evidence of what was sent.</p>
    <div className="mt-4"><Button ref={confirmTrigger} size="sm" variant="outline" onClick={openConfirm}>Confirm materials</Button></div>
    {submissions.isLoading ? <p className="mt-4 text-sm" role="status">Loading submission history…</p> : submissions.isError ? <p className="mt-4 text-sm" role="alert">Could not load submission history. <Button size="sm" variant="outline" onClick={() => void submissions.refetch()}>Retry</Button></p> : history.length ? <ul className="mt-4 space-y-2">{history.map((item: ApplicationSubmissionSummary) => <li key={item.id} className="min-w-0 rounded border p-3 text-sm"><p className="font-medium">{labels[item.material_choice]}</p><p className="text-muted-foreground">{submittedLabel(item.submitted_at_is_user_reported)}: {date(item.submitted_at)} · History recorded: {date(item.created_at)}</p><p className="text-muted-foreground">{item.package_id ? `Package #${item.package_id} · ${item.artifact_ids.length} exact file${item.artifact_ids.length === 1 ? "" : "s"} linked` : "No package or exact files linked"}{item.note ? " · Note recorded" : ""}</p><Button ref={(element) => { if (element) detailButtons.current.set(item.id, element); else detailButtons.current.delete(item.id); }} className="mt-2" size="sm" variant="outline" onClick={(event) => { detailOpener.current = event.currentTarget; deletionCompleted.current = false; setSelectedId(item.id); }}>View details</Button></li>)}</ul> : <p className="mt-4 text-sm text-muted-foreground">Submitted material not recorded.</p>}
    {submissions.hasNextPage ? <Button ref={loadMoreButton} className="mt-3" size="sm" variant="outline" aria-disabled={submissions.isFetchingNextPage} aria-busy={submissions.isFetchingNextPage} onClick={loadMore}>{submissions.isFetchingNextPage ? "Loading…" : "Load more"}</Button> : null}
    <Dialog open={confirmOpen} onOpenChange={(open) => { if (!confirm.isPending) setConfirmOpen(open); }}><DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto" onCloseAutoFocus={(event) => { if (confirmTrigger.current) { event.preventDefault(); confirmTrigger.current.focus(); } }}><DialogHeader><DialogTitle>Confirm submitted materials</DialogTitle><DialogDescription>Record what you actually sent. This history does not infer materials from the current workspace.</DialogDescription></DialogHeader>
      <fieldset className="space-y-2"><legend className="text-sm font-medium">Material used</legend>{(Object.keys(labels) as SubmissionMaterialChoice[]).map((value) => <label key={value} className="flex items-center gap-2 text-sm"><input type="radio" name="material-choice" checked={choice === value} onChange={() => { setChoice(value); setArtifactIds([]); }} />{labels[value]}</label>)}</fieldset>
      {choice === "used_selected_package" ? <div className="space-y-3"><div><label htmlFor="submission-package" className="text-sm font-medium">Frozen package</label><select id="submission-package" className="mt-1 w-full rounded border bg-background p-2 text-sm" value={packageId} onChange={(event) => { setPackageId(event.target.value); setArtifactIds([]); }}><option value="">Choose a package</option>{packages.map((item) => <option key={item.id} value={item.id}>Revision {item.revision} · Package #{item.id} · {date(item.created_at)}</option>)}</select></div>{selectedPackage ? <fieldset className="space-y-2"><legend className="text-sm font-medium">Exact retained files (optional)</legend><p className="text-xs text-muted-foreground">Select only files you know were sent. Zero selections is allowed.</p>{selectedPackage.artifacts?.length ? selectedPackage.artifacts.map((item) => <label key={item.id} className="flex min-w-0 gap-2 text-sm [overflow-wrap:anywhere]"><input type="checkbox" checked={artifactIds.includes(item.id)} onChange={(event) => setArtifactIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} /><span>{item.filename} · {item.document_type.replaceAll("_", " ")} · {item.format.toUpperCase()} · {item.validation_state} · SHA {item.sha256.slice(0, 12)}…</span></label>) : <p className="text-sm text-muted-foreground">No retained files on this package.</p>}</fieldset> : null}</div> : <p className="text-sm text-muted-foreground">{choice === "used_other_material" ? "Different material was used; no prepared package or file will be linked." : "Submitted material is not known; no package or file will be linked."}</p>}
      <div><label htmlFor="submission-time" className="text-sm font-medium">User-reported submitted time (optional)</label><Input id="submission-time" type="datetime-local" value={submittedAt} onChange={(event) => setSubmittedAt(event.target.value)} /></div>
      <div><label htmlFor="submission-note" className="text-sm font-medium">Historical note (optional)</label><Textarea id="submission-note" maxLength={10000} value={note} onChange={(event) => setNote(event.target.value)} /></div>
      <DialogFooter><Button variant="outline" disabled={confirm.isPending} onClick={() => setConfirmOpen(false)}>Cancel</Button><Button disabled={confirm.isPending || (choice === "used_selected_package" && !selectedPackage)} onClick={submit}>{confirm.isPending ? "Recording…" : "Confirm materials"}</Button></DialogFooter>
    </DialogContent></Dialog>
    <Dialog open={selectedId !== null} onOpenChange={(open) => { if (!open) setSelectedId(null); }}><DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto" onCloseAutoFocus={(event) => { event.preventDefault(); if (deletionCompleted.current) historyHeading.current?.focus(); else if (navigatingToPackage.current) { navigatingToPackage.current = false; onPackageNavigationClosed?.(); } else if (detailOpener.current?.isConnected) detailOpener.current.focus(); else historyHeading.current?.focus(); }}><DialogHeader><DialogTitle>Submission details</DialogTitle><DialogDescription>Historical confirmation recorded for this application.</DialogDescription></DialogHeader>
      {detail.isLoading ? <p role="status">Loading details…</p> : detail.isError ? <p role="alert">{detail.error instanceof ApiError && detail.error.status === 404 ? "Submission details no longer available." : "Could not load submission details."}</p> : row ? <div className="min-w-0 space-y-3 text-sm [overflow-wrap:anywhere]"><p className="font-medium">{labels[row.material_choice]}</p><p>{submittedLabel(row.submitted_at_is_user_reported)}: {date(row.submitted_at)}</p><p>History recorded: {date(row.created_at)}</p><p>Provenance: {row.provenance.replaceAll("_", " ")}</p>{row.note ? <p className="whitespace-pre-wrap">Note: {row.note}</p> : null}{row.package_id ? <div><p>Frozen package #{row.package_id}</p><Button size="sm" variant="outline" onClick={() => { navigatingToPackage.current = true; onViewPackage(row.package_id!); setSelectedId(null); }}>Inspect historical package</Button></div> : <p>No package linked.</p>}{row.artifacts.length ? <ul className="space-y-2">{row.artifacts.map((item) => <li className="rounded border p-2" key={item.id}>{item.filename} · {item.document_type.replaceAll("_", " ")} · {item.format.toUpperCase()}<br />Validation: {item.validation_state}<br />SHA-256: <span className="break-all">{item.sha256}</span></li>)}</ul> : <p>No exact files linked.</p>}<Button ref={deleteTrigger} size="sm" variant="destructive" onClick={() => { deletionCompleted.current = false; setDeleteOpen(true); }}>Delete history</Button></div> : null}
    </DialogContent></Dialog>
    <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}><AlertDialogContent onCloseAutoFocus={(event) => { event.preventDefault(); if (deletionCompleted.current) historyHeading.current?.focus(); else deleteTrigger.current?.focus(); }}><AlertDialogHeader><AlertDialogTitle>Delete submission history?</AlertDialogTitle><AlertDialogDescription>This removes the recorded history details. It does not undo an application sent to an employer and does not change application status.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel><AlertDialogAction disabled={remove.isPending} onClick={(event) => { event.preventDefault(); if (selectedId !== null) remove.mutate(selectedId); }}>Delete history</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </section>;
}
