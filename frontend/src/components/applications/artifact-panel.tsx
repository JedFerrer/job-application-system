"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ApiError, applicationsApi } from "@/lib/api";
import { buildCoverLetterDocx, buildCoverLetterPdf, buildResumeDocx, buildResumePdf } from "@/lib/cover-letter-export";
import type { ApplicationPackage, ArtifactDetail, ArtifactDocumentType, ArtifactFormat, ArtifactSummary, ArtifactValidationState, TailoredResume } from "@/lib/types";

const active = (state: ArtifactValidationState) => state === "queued" || state === "validating";
const labels: Record<ArtifactValidationState, string> = {
  queued: "Queued for checking", validating: "Checking file", validated: "Content checks passed",
  warning: "Checked with warnings", failed: "Validation failed", unavailable: "Validation unavailable",
};
const docxValidationCopy: Record<ArtifactValidationState, string> = {
  validated: "Content and structure checked. Page layout, pagination, and clipping were not checked.",
  warning: "Content and structure checked. Page layout, pagination, and clipping were not checked.",
  queued: "Content and structure checks are waiting to run. Page layout, pagination, and clipping are not checked.",
  validating: "Content and structure checks are in progress. Page layout, pagination, and clipping are not checked.",
  failed: "Validation failed before all checks could be confirmed. Review the results below for checks that completed. Page layout, pagination, and clipping were not checked.",
  unavailable: "Validation is unavailable, so content and structure checks could not be confirmed. Page layout, pagination, and clipping were not checked.",
};
const checkLabels: Record<string, string> = {
  content: "Content", content_match: "Content", blank_pages: "Blank pages", text_bounds: "Text bounds",
  page_preference: "Page preference", page_count: "Page count", structure: "Document structure",
};
const categoryLabels: Record<string, string> = {
  unsupported_type: "Unsupported file type", size_exceeded: "File too large", corrupt_pdf: "Damaged PDF",
  encrypted_pdf: "Encrypted PDF", page_limit: "Page limit exceeded", invalid_docx: "Invalid DOCX",
  macro_detected: "Document contains macros", decompression_limit: "Document size limit exceeded",
  content_mismatch: "Content differs from package", parser_unavailable: "File checker unavailable",
  parser_timeout: "File check timed out", quota_exceeded: "Storage quota exceeded",
};
const readable = (value: string) => value.replaceAll("_", " ");
const artifactKey = (applicationId: number, artifactId: number) => ["application-artifact", applicationId, artifactId] as const;
const packageKey = (applicationId: number, packageId: number) => ["application-package", applicationId, packageId] as const;
const workspaceKey = (applicationId: number) => ["application-workspace", applicationId] as const;

function saveError(error: unknown) {
  if (!(error instanceof ApiError)) return "Could not save file. Check your connection and try again.";
  if (error.status === 413) return "Could not save file. It exceeds the file size or application storage limit.";
  if (error.status === 415) return "Could not save file. The generated format is unsupported.";
  if (error.status === 422) return "Could not save file. The generated file could not be accepted or read.";
  if (error.status === 409) return "Could not save file. The package changed; refresh and try again.";
  if (error.status === 503) return "Could not save file. Validation is temporarily unavailable; try again.";
  return "Could not save file. Try again.";
}

function frozenResume(item: ApplicationPackage): TailoredResume {
  const resume = item.content.resume;
  const content = resume && typeof resume === "object" && !Array.isArray(resume) ? (resume as Record<string, unknown>).content : null;
  if (!content || typeof content !== "object" || Array.isArray(content)) throw new Error("Frozen resume is unavailable.");
  const value = content as Record<string, unknown>;
  if (typeof value.job_id !== "number" || !Array.isArray(value.work_history) || !Array.isArray(value.education)
    || !Array.isArray(value.certifications) || !Array.isArray(value.matched_skills)
    || !Array.isArray(value.other_skills) || !Array.isArray(value.missing_keywords)) {
    throw new Error("Frozen resume is unavailable.");
  }
  return value as unknown as TailoredResume;
}

function frozenLetter(item: ApplicationPackage): string | null {
  const letter = item.content.cover_letter;
  if (!letter || typeof letter !== "object" || Array.isArray(letter)) return null;
  const value = letter as Record<string, unknown>;
  return value.mode === "selected" && typeof value.content === "string" && value.content.trim() ? value.content : null;
}

function browserDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function ArtifactRow({ applicationId, item, refresh }: { applicationId: number; item: ArtifactSummary; refresh: (packageId: number) => Promise<void> }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"download" | "retry" | "delete" | null>(null);
  const [message, setMessage] = useState("");
  const [now, setNow] = useState(Date.now());
  const mountedRow = useRef(true);
  useEffect(() => { mountedRow.current = true; return () => { mountedRow.current = false; }; }, []);
  const detail = useQuery({
    queryKey: artifactKey(applicationId, item.id),
    queryFn: async () => {
      const result = await applicationsApi.getArtifact(applicationId, item.id);
      if (!mountedRow.current && active(item.validation_state) && !active(result.validation_state)) {
        await refresh(item.package_id);
      }
      return result;
    },
    enabled: open || active(item.validation_state),
    retry: false,
    refetchInterval: (query) => {
      const state = !active(item.validation_state) && query.state.data && active(query.state.data.validation_state)
        ? item.validation_state : query.state.data?.validation_state ?? item.validation_state;
      return active(state) ? 3000 : false;
    },
    refetchOnWindowFocus: true,
  });
  const state = !active(item.validation_state) && detail.data && active(detail.data.validation_state)
    ? item.validation_state : detail.data?.validation_state ?? item.validation_state;
  const previouslyActive = useRef(active(item.validation_state));
  useEffect(() => {
    if (previouslyActive.current && !active(state)) {
      previouslyActive.current = false;
      void refresh(item.package_id);
    } else if (active(state)) previouslyActive.current = true;
  }, [state, item.package_id, refresh]);
  useEffect(() => {
    if (!active(state) || !detail.data?.retry_after || now >= Date.parse(detail.data.retry_after)) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [state, detail.data?.retry_after, now]);

  const download = async () => {
    setBusy("download"); setMessage("");
    try {
      const stored = await applicationsApi.downloadArtifact(applicationId, item.id, item.filename);
      browserDownload(stored.blob, stored.filename);
    } catch { setMessage("Could not download the saved file. Try Download again."); }
    finally { setBusy(null); }
  };
  const canRetry = state === "failed" || state === "unavailable" ||
    (active(state) && !!detail.data?.retry_after && now >= Date.parse(detail.data.retry_after));
  const retry = async () => {
    setBusy("retry"); setMessage("");
    try {
      const result = await applicationsApi.retryArtifactValidation(applicationId, item.id);
      client.setQueryData(artifactKey(applicationId, item.id), result.artifact);
      await refresh(item.package_id);
      setMessage("Validation queued for this saved file.");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await client.invalidateQueries({ queryKey: artifactKey(applicationId, item.id) });
        await refresh(item.package_id);
        setMessage("Validation is still in progress. File state refreshed.");
      } else setMessage(error instanceof ApiError && error.status === 503 ? "Validation is temporarily unavailable. Try again." : "Could not retry validation. Try again.");
    } finally { setBusy(null); }
  };
  const remove = async () => {
    if (!window.confirm(`Delete the exported file “${item.filename}”? The package and source documents will remain.`)) return;
    setBusy("delete"); setMessage("");
    try {
      await applicationsApi.deleteArtifact(applicationId, item.id);
      client.removeQueries({ queryKey: artifactKey(applicationId, item.id) });
      await refresh(item.package_id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await client.invalidateQueries({ queryKey: artifactKey(applicationId, item.id) });
        await refresh(item.package_id);
        setMessage("This file is still being checked or cannot be deleted. File state refreshed.");
      } else setMessage("Could not delete the exported file. Try again.");
    } finally { setBusy(null); }
  };
  const report = detail.data?.validation_report;
  return <li className="min-w-0 rounded-md border p-3">
    <p className="break-all font-medium">{item.filename}</p>
    <p className="mt-1 text-sm text-muted-foreground">{item.document_type === "resume" ? "Resume" : "Cover letter"} · {item.format.toUpperCase()} · {new Date(item.created_at).toLocaleString()} · {item.byte_length.toLocaleString()} bytes</p>
    <p className="mt-1 text-sm">{labels[state]}{state === "failed" ? " · File saved; validation failed" : ""}</p>
    <p className="mt-1 text-xs text-muted-foreground">SHA-256: {item.sha256.slice(0, 12)}…</p>
    <div className="mt-3 flex flex-wrap gap-2">
      <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void download()}>Download</Button>
      <Button size="sm" variant="outline" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide report" : "View report"}</Button>
      {(state === "failed" || state === "unavailable" || active(state)) && <Button size="sm" variant="outline" disabled={!canRetry || busy !== null} onClick={() => void retry()}>Retry validation</Button>}
      {!active(state) && <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void remove()}>Delete exported file</Button>}
    </div>
    {message && <p className="mt-2 text-sm" role="status">{message}</p>}
    {open && <div className="mt-3 space-y-2 border-t pt-3 text-sm">
      {detail.isLoading ? <p>Loading report…</p> : detail.isError ? <p role="alert">Could not load report. Close and open it to retry.</p> : <>
        <p>State: {labels[state]}</p><p className="break-all">Full SHA-256: {item.sha256}</p>
        {item.format === "docx" && <p>{docxValidationCopy[state]}</p>}
        {report?.page_count != null && <p>Page count: {report.page_count}</p>}
        {report?.failure_category && <p>Reason: {categoryLabels[report.failure_category] ?? readable(report.failure_category)}</p>}
        {report?.checks.length ? <ul className="list-inside list-disc">{report.checks.map((check, index) => <li key={`${check.name}-${index}`}>{checkLabels[check.name] ?? readable(check.name)}: {readable(check.status)}{check.category ? ` · ${categoryLabels[check.category] ?? readable(check.category)}` : ""}</li>)}</ul> : <p>No check results available yet.</p>}
      </>}
    </div>}
  </li>;
}

export function ArtifactPanel({ applicationId, package: item, current }: { applicationId: number; package: ApplicationPackage; current: boolean }) {
  const client = useQueryClient();
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [message, setMessage] = useState("");
  const inFlight = useRef(new Set<string>());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const refresh = async (packageId: number) => {
    await Promise.all([
      client.invalidateQueries({ queryKey: packageKey(applicationId, packageId) }),
      client.invalidateQueries({ queryKey: workspaceKey(applicationId) }),
    ]);
  };
  const generate = async (documentType: ArtifactDocumentType, format: ArtifactFormat) => {
    const captured = { applicationId, packageId: item.id, packageHash: item.content_sha256, documentType, format, source: item };
    const key = `${captured.packageId}:${documentType}:${format}`;
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key);
    setBusy((value) => ({ ...value, [key]: true }));
    setMessage("");
    try {
      const stem = documentType === "resume" ? `resume_package_${captured.packageId}` : `cover_letter_package_${captured.packageId}`;
      const built = documentType === "resume"
        ? format === "pdf" ? buildResumePdf(frozenResume(captured.source), stem) : await buildResumeDocx(frozenResume(captured.source), stem)
        : format === "pdf" ? buildCoverLetterPdf(frozenLetter(captured.source)!, stem) : await buildCoverLetterDocx(frozenLetter(captured.source)!, stem);
      const result = await applicationsApi.uploadArtifact(captured.applicationId, captured.packageId, built.blob, built.filename, documentType, format, built.exporterVersion);
      client.setQueryData<ArtifactDetail>(artifactKey(captured.applicationId, result.artifact.id), result.artifact);
      await refresh(captured.packageId);
      if (mounted.current) setMessage(result.existing ? "This exact file is already saved." : `File saved; ${labels[result.artifact.validation_state].toLowerCase()}.`);
      try {
        const stored = await applicationsApi.downloadArtifact(captured.applicationId, result.artifact.id, result.artifact.filename);
        browserDownload(stored.blob, stored.filename);
      } catch {
        if (mounted.current) setMessage("File saved, but download failed. Use Download on the saved file to try again.");
      }
    } catch (error) {
      if (mounted.current) setMessage(error instanceof Error && error.message === "Frozen resume is unavailable." ? error.message : saveError(error));
      if (error instanceof ApiError && error.status === 409) await refresh(captured.packageId);
    } finally {
      inFlight.current.delete(key);
      if (mounted.current) setBusy((value) => ({ ...value, [key]: false }));
    }
  };
  const letter = frozenLetter(item);
  const rows = [...(item.artifacts ?? [])].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id);
  return <section className="rounded-lg border bg-card p-4" aria-label="Exported files">
    <h2 className="font-semibold">Exported files</h2>
    {current ? <div className="mt-3 space-y-3"><p className="text-sm text-muted-foreground">Generate new file from this frozen package.</p>
      <div className="flex flex-wrap gap-2">{(["pdf", "docx"] as const).map((format) => <Button key={`resume-${format}`} size="sm" disabled={!!busy[`${item.id}:resume:${format}`]} onClick={() => void generate("resume", format)}>Generate Resume {format.toUpperCase()}</Button>)}
        {letter && (["pdf", "docx"] as const).map((format) => <Button key={`cover-${format}`} size="sm" disabled={!!busy[`${item.id}:cover_letter:${format}`]} onClick={() => void generate("cover_letter", format)}>Generate Cover Letter {format.toUpperCase()}</Button>)}
      </div>
    </div> : <p className="mt-2 text-sm text-muted-foreground">Historical package files are available for download. New files can be generated from the current package.</p>}
    {message && <p className="mt-3 text-sm" role="status">{message}</p>}
    {rows.length ? <ul className="mt-4 space-y-3">{rows.map((artifact) => <ArtifactRow key={artifact.id} applicationId={applicationId} item={artifact} refresh={refresh} />)}</ul> : <p className="mt-4 text-sm text-muted-foreground">No exported files saved for this package.</p>}
  </section>;
}
