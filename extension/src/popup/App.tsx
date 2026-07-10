import { useState, useEffect, useCallback } from "react";
import type { ExtensionMessage, ExtractedJob, AutofillProfile, FieldMapping, DetectedField } from "../shared/types";
import { sendMessage } from "../shared/messages";
import ConnectPage from "./pages/ConnectPage";
import JobDetectedPage from "./pages/JobDetectedPage";
import JobImportedPage from "./pages/JobImportedPage";
import AutofillPreviewPage from "./pages/AutofillPreviewPage";
import ConfirmationPage from "./pages/ConfirmationPage";

type Page =
  | { name: "loading" }
  | { name: "connect" }
  | { name: "connected"; user: { id: number; email: string; name: string } }
  | { name: "job_detected"; job: ExtractedJob }
  | { name: "job_imported"; job: ExtractedJob; response: { job_id: number; match_status: string } }
  | { name: "autofill_preview"; profile: AutofillProfile; fields: DetectedField[] }
  | { name: "confirmation"; pageUrl: string }
  | { name: "error"; message: string };

export default function App() {
  const [page, setPage] = useState<Page>({ name: "loading" });

  // On mount, check session state and restore persisted context
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await sendMessage({ type: "GET_SESSION_STATE" });
        if (cancelled) return;
        if (res.type === "SESSION_STATE" && res.payload.connected && res.payload.user) {
          // Restore persisted job/fields from chrome.storage.session
          const stored = await chrome.storage.session.get(["detected_job", "detected_fields", "detected_fields_domain"]);
          if (stored.detected_job) {
            setPage({ name: "job_detected", job: stored.detected_job as ExtractedJob });
          } else {
            setPage({ name: "connected", user: res.payload.user });
          }
        } else {
          setPage({ name: "connect" });
        }
      } catch {
        if (!cancelled) setPage({ name: "connect" });
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Listen for messages from service worker (e.g., job detected, form detected)
  useEffect(() => {
    const handler = (message: ExtensionMessage) => {
      switch (message.type) {
        case "JOB_DETECTED":
          setPage({ name: "job_detected", job: message.payload });
          break;
        case "JOB_IMPORTED":
          // Keep the current job context if available
          setPage((prev) =>
            prev.name === "job_detected"
              ? { name: "job_imported", job: prev.job, response: message.payload }
              : prev,
          );
          break;
        case "AUTOFILL_PROFILE":
          // Profile loaded — ready for preview
          break;
        case "SHOW_CONFIRMATION_PROMPT":
          setPage({ name: "confirmation", pageUrl: message.payload.pageUrl });
          break;
        case "ERROR":
          setPage({ name: "error", message: message.payload.message });
          break;
      }
    };
    chrome.runtime.onMessage.addListener(handler);
    return () => chrome.runtime.onMessage.removeListener(handler);
  }, []);

  const handleImport = useCallback(async (job: ExtractedJob) => {
    try {
      const res = await sendMessage({ type: "IMPORT_JOB", payload: job });
      if (res.type === "JOB_IMPORTED") {
        setPage({ name: "job_imported", job, response: res.payload });
      } else if (res.type === "ERROR") {
        setPage({ name: "error", message: res.payload.message });
      }
    } catch (e) {
      setPage({ name: "error", message: e instanceof Error ? e.message : "Import failed" });
    }
  }, []);

  const handleMarkApplied = useCallback(async (jobUrl: string) => {
    try {
      await sendMessage({ type: "MARK_APPLIED", payload: { jobUrl } });
      window.close();
    } catch (e) {
      setPage({ name: "error", message: e instanceof Error ? e.message : "Failed to mark as applied" });
    }
  }, []);

  const handleDidNotSubmit = useCallback(async () => {
    await sendMessage({ type: "DID_NOT_SUBMIT" });
    window.close();
  }, []);

  const handleLogout = useCallback(async () => {
    await sendMessage({ type: "LOGOUT" });
    setPage({ name: "connect" });
  }, []);

  // Render the current page
  switch (page.name) {
    case "loading":
      return <LoadingView />;
    case "connect":
      return <ConnectPage onConnected={(user) => setPage({ name: "connected", user })} />;
    case "connected":
      return (
        <ConnectedView
          user={page.user}
          onLogout={handleLogout}
        />
      );
    case "job_detected":
      return <JobDetectedPage job={page.job} onImport={handleImport} />;
    case "job_imported":
      return <JobImportedPage job={page.job} response={page.response} />;
    case "autofill_preview":
      return (
        <AutofillPreviewPage
          profile={page.profile}
          fields={page.fields}
          onFill={() => {
            /* handled via message */
          }}
        />
      );
    case "confirmation":
      return (
        <ConfirmationPage
          pageUrl={page.pageUrl}
          onMarkApplied={() => handleMarkApplied(page.pageUrl)}
          onNotYet={handleDidNotSubmit}
        />
      );
    case "error":
      return <ErrorView message={page.message} onDismiss={() => setPage({ name: "connect" })} />;
    default:
      return <LoadingView />;
  }
}

// ── Sub-views ──────────────────────────────────────────────────────────────

function LoadingView() {
  return (
    <div style={{ textAlign: "center", padding: "24px 0" }}>
      <p style={{ color: "var(--muted)" }}>Loading…</p>
    </div>
  );
}

function ConnectedView({
  user,
  onLogout,
}: {
  user: { id: number; email: string; name: string };
  onLogout: () => void;
}) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <p style={{ fontWeight: 600 }}>CareerOS Assistant</p>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>{user.email}</p>
        </div>
        <button
          onClick={onLogout}
          style={{
            background: "none",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "4px 10px",
            fontSize: 12,
            cursor: "pointer",
            color: "var(--muted)",
          }}
        >
          Disconnect
        </button>
      </div>
      <div
        style={{
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 16,
          textAlign: "center",
        }}
      >
        <p style={{ color: "var(--muted)", fontSize: 13 }}>
          Navigate to a job posting on LinkedIn, Indeed, Greenhouse, or Lever to import it.
        </p>
      </div>
    </div>
  );
}

function ErrorView({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div style={{ textAlign: "center", padding: "16px 0" }}>
      <p style={{ color: "var(--destructive)", marginBottom: 12 }}>{message}</p>
      <button
        onClick={onDismiss}
        style={{
          background: "var(--accent)",
          color: "var(--accent-fg)",
          border: "none",
          borderRadius: 6,
          padding: "8px 16px",
          cursor: "pointer",
          fontSize: 13,
        }}
      >
        Try Again
      </button>
    </div>
  );
}
