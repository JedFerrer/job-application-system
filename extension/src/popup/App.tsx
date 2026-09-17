import { useState, useEffect, useCallback } from "react";
import type { AttemptBinding, AttemptCandidate, AutofillProfile, DetectedField, ExtensionMessage, ExtractedJob, FieldMapping, FillExecutionResult, TabAttemptContext } from "../shared/types";
import { sendMessage } from "../shared/messages";
import ConnectPage from "./pages/ConnectPage";
import JobDetectedPage from "./pages/JobDetectedPage";
import JobImportedPage from "./pages/JobImportedPage";
import AutofillPreviewPage from "./pages/AutofillPreviewPage";
import ConfirmationPage from "./pages/ConfirmationPage";

type User = { id: number; email: string; name: string };
type Page =
  | { name: "loading" } | { name: "connect" } | { name: "connected"; user: User }
  | { name: "attempts"; candidates: AttemptCandidate[] } | { name: "bound"; context: TabAttemptContext; fields: DetectedField[]; error?: string }
  | { name: "job_detected"; job: ExtractedJob } | { name: "job_imported"; job: ExtractedJob; response: { job_id: number; match_status: string } }
  | { name: "autofill_preview"; context: TabAttemptContext; profile: AutofillProfile; fields: DetectedField[]; filling: boolean; execution?: FillExecutionResult; error?: string }
  | { name: "confirmation"; pageUrl: string } | { name: "error"; message: string };

export default function App() {
  const [page, setPage] = useState<Page>({ name: "loading" });

  const restore = useCallback(async (error?: string): Promise<void> => {
    const session = await sendMessage({ type: "GET_SESSION_STATE" });
    if (session.type !== "SESSION_STATE" || !session.payload.connected || !session.payload.user) { setPage({ name: "connect" }); return; }
    const tab = await sendMessage({ type: "GET_TAB_CONTEXT" });
    if (tab.type === "TAB_CONTEXT" && tab.payload.context) {
      setPage({ name: "bound", context: tab.payload.context, fields: tab.payload.fields ?? [], error });
    } else setPage({ name: "connected", user: session.payload.user });
  }, []);

  useEffect(() => { void restore().catch(() => setPage({ name: "connect" })); }, [restore]);
  useEffect(() => {
    if (!chrome.storage?.onChanged) return;
    const listener = (_changes: Record<string, chrome.storage.StorageChange>, area: string) => { if (area === "session") void restore().catch(() => setPage({ name: "connect" })); };
    chrome.storage.onChanged.addListener(listener);
    return () => chrome.storage.onChanged.removeListener(listener);
  }, [restore]);
  useEffect(() => {
    const handler = (message: ExtensionMessage) => {
      if (message.type === "JOB_DETECTED") setPage({ name: "job_detected", job: message.payload });
      if (message.type === "SHOW_CONFIRMATION_PROMPT") setPage({ name: "confirmation", pageUrl: message.payload.pageUrl });
      if (message.type === "ERROR") setPage({ name: "error", message: message.payload.message });
    };
    chrome.runtime.onMessage.addListener(handler);
    return () => chrome.runtime.onMessage.removeListener(handler);
  }, []);

  const handleReview = useCallback(async () => {
    try {
      const tab = await sendMessage({ type: "GET_TAB_CONTEXT" });
      if (tab.type !== "TAB_CONTEXT" || !tab.payload.context || tab.payload.context.bindingState !== "bound" || !tab.payload.fields?.length) { await restore(); return; }
      const profile = await sendMessage({ type: "GET_AUTOFILL_PROFILE" });
      if (profile.type !== "AUTOFILL_PROFILE") { await restore(); return; }
      setPage({ name: "autofill_preview", context: tab.payload.context, profile: profile.payload, fields: tab.payload.fields, filling: false });
    } catch { await restore(); }
  }, [restore]);

  const handleFill = useCallback(async (fields: DetectedField[], mappings: FieldMapping[]) => {
    setPage((current) => current.name === "autofill_preview" ? { ...current, filling: true, error: undefined } : current);
    try {
      const result = await sendMessage({ type: "FILL_FORM", payload: { fields, mappings } });
      if (result.type !== "AUTOFILL_COMPLETE" || !isValidExecution(result.payload, mappings.length)) throw new Error("Autofill could not be completed safely");
      setPage((current) => current.name === "autofill_preview" ? { ...current, filling: false, execution: result.payload } : current);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Autofill could not be completed";
      setPage((current) => current.name === "autofill_preview" ? { ...current, filling: false, error: message } : current);
      await restore(message).catch(() => setPage({ name: "error", message }));
    }
  }, [restore]);

  const importJob = useCallback(async (job: ExtractedJob) => { const result = await sendMessage({ type: "IMPORT_JOB", payload: job }); if (result.type === "JOB_IMPORTED") setPage({ name: "job_imported", job, response: result.payload }); }, []);
  const bind = useCallback(async (candidate: AttemptBinding) => { const result = await sendMessage({ type: "BIND_ATTEMPT", payload: candidate }); if (result.type === "TAB_CONTEXT" && result.payload.context) setPage({ name: "bound", context: result.payload.context, fields: result.payload.fields ?? [] }); }, []);
  const resolve = useCallback(async () => { const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true }); if (!tab?.url) return; const result = await sendMessage({ type: "RESOLVE_ATTEMPTS", payload: { pageUrl: tab.url } }); if (result.type === "ATTEMPT_CANDIDATES") setPage({ name: "attempts", candidates: result.payload.candidates }); }, []);

  switch (page.name) {
    case "loading": return <LoadingView />;
    case "connect": return <ConnectPage onConnected={(user) => setPage({ name: "connected", user })} />;
    case "connected": return <ConnectedView user={page.user} onLogout={() => { void sendMessage({ type: "LOGOUT" }); setPage({ name: "connect" }); }} onResolve={resolve} />;
    case "attempts": return <AttemptSelection candidates={page.candidates} onSelect={bind} onBack={() => void restore()} />;
    case "bound": return <BoundView context={page.context} fields={page.fields} error={page.error} onReview={() => void handleReview()} onConfirm={() => void bind(page.context)} onClear={() => { void sendMessage({ type: "CLEAR_TAB_CONTEXT" }); void restore(); }} />;
    case "job_detected": return <JobDetectedPage job={page.job} onImport={(job) => void importJob(job)} />;
    case "job_imported": return <JobImportedPage job={page.job} response={page.response} />;
    case "autofill_preview": return <AutofillPreviewPage profile={page.profile} fields={page.fields} filling={page.filling} execution={page.execution} error={page.error} onFill={(mappings) => void handleFill(page.fields, mappings)} onBack={() => void restore()} />;
    case "confirmation": return <ConfirmationPage pageUrl={page.pageUrl} onMarkApplied={() => { void sendMessage({ type: "MARK_APPLIED", payload: { jobUrl: page.pageUrl } }); window.close(); }} onNotYet={() => { void sendMessage({ type: "DID_NOT_SUBMIT" }); window.close(); }} />;
    case "error": return <ErrorView message={page.message} onDismiss={() => void restore()} />;
  }
}

function isValidExecution(result: FillExecutionResult, mappings: number): boolean { return result.attempted === result.filled + result.failed && result.attempted + result.skipped === mappings; }
function LoadingView() { return <div style={{ textAlign: "center", padding: "24px 0" }}><p style={{ color: "var(--muted)" }}>Loading…</p></div>; }
function ConnectedView({ user, onLogout, onResolve }: { user: User; onLogout: () => void; onResolve: () => void }) { return <div><div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}><div><p style={{ fontWeight: 600 }}>CareerOS Assistant</p><p style={{ fontSize: 12, color: "var(--muted)" }}>{user.email}</p></div><button onClick={onLogout}>Disconnect</button></div><p style={{ color: "var(--muted)", fontSize: 13 }}>Navigate to a job posting on LinkedIn, Indeed, Greenhouse, or Lever to import it.</p><button onClick={onResolve}>Find application for this tab</button></div>; }
function AttemptSelection({ candidates, onSelect, onBack }: { candidates: AttemptCandidate[]; onSelect: (candidate: AttemptBinding) => void; onBack: () => void }) { return candidates.length ? <div><p>{candidates.length === 1 ? "Confirm this application for the current tab" : "Choose the application for the current tab"}</p>{candidates.map((candidate) => <button key={candidate.attemptId} onClick={() => onSelect(candidate)}><strong>{candidate.role}</strong><br />{candidate.company ?? "Company unavailable"}</button>)}</div> : <div><p>No matching application handoff was found for this page.</p><button onClick={onBack}>Back</button></div>; }
function BoundView({ context, fields, error, onReview, onConfirm, onClear }: { context: TabAttemptContext; fields: DetectedField[]; error?: string; onReview: () => void; onConfirm: () => void; onClear: () => void }) { if (context.bindingState === "reconfirmation_required") return <div><p>Application #{context.applicationId} is recognized for this tab. Confirm before filling or tracking continues.</p><button onClick={onConfirm}>Confirm this application</button><button onClick={onClear}>Clear tab association</button></div>; return <div><p>This tab is bound to application #{context.applicationId}.</p>{fields.length > 0 && <button onClick={onReview}>Review autofill</button>}{error && <p style={{ color: "var(--destructive)" }}>{error}</p>}<button onClick={onClear}>Clear tab association</button></div>; }
function ErrorView({ message, onDismiss }: { message: string; onDismiss: () => void }) { return <div><p style={{ color: "var(--destructive)" }}>{message}</p><button onClick={onDismiss}>Try Again</button></div>; }
