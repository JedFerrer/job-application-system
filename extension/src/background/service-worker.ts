// ── Background Service Worker ──────────────────────────────────────────────
// Message routing hub: receives messages from popup and content scripts,
// calls the backend API, and returns responses. Also handles long-lived
// extension lifecycle (install, startup).

import { onMessage } from "../shared/messages";
import type { ExtensionMessage } from "../shared/types";
import { apiRequest } from "./api-client";
import { completePairing, getSessionState, logout } from "./auth-manager";
import { EVENT_TYPES } from "../shared/constants";
import { clearTabContext, getAttemptContext, getDetectedFields, setAttemptContext, setDetectedFields } from "./tab-context";
import type { TabAttemptContext } from "./tab-context";

// ── Lifecycle ──────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log("[CareerOS] Extension installed");
});

chrome.runtime.onStartup?.addListener(() => {
  void restoreTabContexts();
});

chrome.tabs.onRemoved.addListener((tabId) => { void handleTabRemoved(tabId); });
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  void handleTabUpdated(tabId, changeInfo);
});

export async function restoreTabContexts(): Promise<void> {
  const tabs = await chrome.tabs.query({});
  await Promise.all(tabs.flatMap((tab) =>
    tab.id !== undefined && tab.url ? [reconcileNavigation(tab.id, tab.url)] : [],
  ));
}

export async function handleTabRemoved(tabId: number): Promise<void> {
  await clearTabContext(tabId);
}

export async function handleTabUpdated(
  tabId: number, changeInfo: chrome.tabs.TabChangeInfo,
): Promise<void> {
  if (changeInfo.url) await reconcileNavigation(tabId, changeInfo.url);
}

type AttemptResolution = { candidates: Array<{ attempt_id: string }> };
type NavigationTransition = "retain_bound" | "require_reconfirmation" | "clear";

function evaluateNavigationForContext(
  context: TabAttemptContext,
  destination: URL,
  resolution: AttemptResolution,
): NavigationTransition {
  if (!resolution.candidates.some((candidate) => candidate.attempt_id === context.attemptId)) {
    return "clear";
  }
  if (context.bindingState === "reconfirmation_required" || destination.host !== context.host) {
    return "require_reconfirmation";
  }
  return "retain_bound";
}

async function currentContextForNavigation(
  tabId: number,
  url: string,
  attemptId: string,
): Promise<TabAttemptContext | null> {
  const [context, tab] = await Promise.all([
    getAttemptContext(tabId),
    chrome.tabs.get(tabId).catch(() => undefined),
  ]);
  if (!context || context.attemptId !== attemptId || tab?.url !== url) return null;
  return context;
}

async function reconcileNavigation(tabId: number, url: string): Promise<TabAttemptContext | null> {
  const initialContext = await getAttemptContext(tabId);
  if (!initialContext) return null;
  let parsed: URL;
  try { parsed = new URL(url); } catch { await clearTabContext(tabId); return null; }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    await clearTabContext(tabId);
    return null;
  }
  try {
    const resolution = await apiRequest<AttemptResolution>(
      `/extension/applications/attempts?url=${encodeURIComponent(safeResolutionUrl(parsed))}`,
    );
    const context = await currentContextForNavigation(tabId, url, initialContext.attemptId);
    if (!context) return await getAttemptContext(tabId);
    switch (evaluateNavigationForContext(context, parsed, resolution)) {
      case "clear":
        await clearTabContext(tabId);
        return null;
      case "require_reconfirmation": {
        const updated = { ...context, host: parsed.host, bindingState: "reconfirmation_required" as const };
        await setAttemptContext(tabId, updated);
        await setDetectedFields(tabId, []);
        return updated;
      }
      case "retain_bound":
        return context;
    }
  } catch {
    const context = await currentContextForNavigation(tabId, url, initialContext.attemptId);
    if (context) {
      await clearTabContext(tabId);
    }
    return context ? null : await getAttemptContext(tabId);
  }
}

function safeResolutionUrl(parsed: URL): string {
  // Never forward arbitrary navigation query/fragment state. These are the
  // only query identities modeled by Unit 1C; employer ATS routes are path-based.
  const allowed = new URLSearchParams();
  if (parsed.hostname.endsWith("indeed.com") && parsed.pathname.replace(/\/$/, "") === "/viewjob") {
    const jk = parsed.searchParams.get("jk"); if (jk) allowed.set("jk", jk);
  }
  if (parsed.hostname.endsWith("linkedin.com")) {
    const currentJobId = parsed.searchParams.get("currentJobId"); if (currentJobId) allowed.set("currentJobId", currentJobId);
  }
  return `${parsed.protocol}//${parsed.host}${parsed.pathname}${allowed.size ? `?${allowed}` : ""}`;
}

async function currentBoundContentContext(tabId: number | undefined, pageUrl: string): Promise<TabAttemptContext> {
  if (tabId === undefined) throw new Error("Select an application for this tab before recording submission");
  const tab = await chrome.tabs.get(tabId);
  if (!tab.url) throw new Error("Select an application for this tab before recording submission");
  let livePageUrl: string;
  let messagePageUrl: string;
  try {
    livePageUrl = safeResolutionUrl(new URL(tab.url));
    messagePageUrl = safeResolutionUrl(new URL(pageUrl));
  } catch {
    throw new Error("Select an application for this tab before recording submission");
  }
  if (livePageUrl !== messagePageUrl) {
    throw new Error("The application page changed before recording submission");
  }
  const storedContext = await getAttemptContext(tabId);
  if (!storedContext || storedContext.bindingState !== "bound") {
    throw new Error("Confirm the application for this tab before recording submission");
  }
  const context = await reconcileNavigation(tabId, tab.url);
  if (!context || context.bindingState !== "bound") {
    throw new Error("Confirm the application for this tab before recording submission");
  }
  return context;
}

// ── Message Router ─────────────────────────────────────────────────────────

onMessage((msg, sender, sendResponse) => {
  handleRuntimeMessage(msg, sender.tab?.id)
    .then((response) => sendResponse(response))
    .catch((err) => {
      console.error("[CareerOS] Message handler error:", err);
      sendResponse({
        type: "ERROR",
        payload: {
          message: err instanceof Error ? err.message : "Unknown error",
        },
      });
    });
  return true; // Keep the message channel open for async response
});

export async function handleRuntimeMessage(msg: ExtensionMessage, tabId?: number): Promise<ExtensionMessage> {
  const activeTabId = async (): Promise<number | undefined> => {
    if (tabId !== undefined) return tabId;
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    return tab?.id;
  };
  switch (msg.type) {
    // ── Session ──────────────────────────────────────────────────────
    case "GET_SESSION_STATE": {
      const state = await getSessionState();
      return { type: "SESSION_STATE", payload: state };
    }

    case "START_PAIRING": {
      const state = await completePairing(msg.pairingCode);
      return { type: "SESSION_STATE", payload: state };
    }

    case "LOGOUT": {
      await logout();
      return { type: "SESSION_STATE", payload: { connected: false } };
    }

    // ── Job Import ───────────────────────────────────────────────────
    case "IMPORT_JOB": {
      // Convert camelCase (extension types) to snake_case (backend API)
      const job = msg.payload;
      const data = await apiRequest<{
        job_id: number;
        application_id: string | null;
        status: string;
        was_existing_job: boolean;
        was_existing_application: boolean;
        match_status: string;
      }>("/extension/jobs/import", {
        method: "POST",
        body: JSON.stringify({
          title: job.title,
          company: job.company,
          location: job.location,
          description: job.description,
          salary_text: job.salaryText,
          source_url: job.sourceUrl,
          source: job.source,
          ats_type: job.atsType,
          external_apply_url: job.externalApplyUrl,
          raw_html_hash: job.rawHtmlHash,
        }),
      });
      return { type: "JOB_IMPORTED", payload: data };
    }

    // ── Apply Click Tracking ─────────────────────────────────────────
    case "APPLY_CLICKED": {
      // Import job first if not already imported, then record event
      // The backend handles dedup — we just forward the event
      const data = await apiRequest<{
        application_id: string;
        job_id: number;
        status: string;
        event_recorded: boolean;
      }>("/extension/applications/events", {
        method: "POST",
        body: JSON.stringify({
          job_url: msg.payload.pageUrl,
          event_type: EVENT_TYPES.APPLY_CLICKED,
          page_url: msg.payload.pageUrl,
          metadata: {
            button_text: msg.payload.buttonText,
            href: msg.payload.href,
          },
        }),
      });
      return { type: "EVENT_RECORDED", payload: data };
    }

    // ── Form Detection ───────────────────────────────────────────────
    case "FORM_DETECTED": {
      // Record form_detected event, then store fields for popup persistence
      const pageUrl = (msg.payload as { fields: unknown; domain: string; atsType?: string; pageUrl?: string }).pageUrl
        ?? (typeof location !== "undefined" ? location.href : "");
      const context = tabId === undefined ? null : await getAttemptContext(tabId);
      const fields = (msg.payload as { fields: unknown[] }).fields;
      if (fields.length > 0) {
        await apiRequest("/extension/applications/events", {
          method: "POST",
          body: JSON.stringify({
            event_type: EVENT_TYPES.FORM_DETECTED,
            page_url: pageUrl,
            attempt_id: context?.attemptId,
            event_id: crypto.randomUUID(),
            metadata: {
              domain: msg.payload.domain,
              ats_type: msg.payload.atsType,
              field_count: fields.length,
            },
          }),
        });
      }
      // Persist detected fields for popup
      if (tabId !== undefined) await setDetectedFields(tabId, fields);
      return { type: "EVENT_RECORDED", payload: { application_id: "", job_id: 0, status: "saved", event_recorded: true } };
    }

    // ── Mark as Applied ──────────────────────────────────────────────
    case "MARK_APPLIED": {
      const targetTabId = await activeTabId();
      const context = targetTabId === undefined ? null : await getAttemptContext(targetTabId);
      if (context?.bindingState === "reconfirmation_required") {
        throw new Error("Confirm the application for this tab before marking it applied");
      }
      await apiRequest("/extension/applications/mark-applied", {
        method: "POST",
        body: JSON.stringify({
          job_url: msg.payload.jobUrl,
          confirmation_source: "user_marked_applied",
          attempt_id: context?.attemptId,
          event_id: crypto.randomUUID(),
        }),
      });
      return { type: "MARK_APPLIED_RESULT", payload: { success: true } };
    }

    case "DID_NOT_SUBMIT": {
      // No backend action needed
      return { type: "MARK_APPLIED_RESULT", payload: { success: false } };
    }

    // ── Autofill ─────────────────────────────────────────────────────
    case "GET_AUTOFILL_PROFILE": {
      const data = await apiRequest("/extension/profile/autofill") as unknown as import("../shared/types").AutofillProfile;
      return { type: "AUTOFILL_PROFILE", payload: data };
    }

    case "FILL_FORM": {
      const targetTabId = await activeTabId();
      const context = targetTabId === undefined ? null : await getAttemptContext(targetTabId);
      if (!context || context.bindingState !== "bound" || !context.boundPageUrl) {
        throw new Error("Select an application for this tab before filling");
      }
      const currentTab = await chrome.tabs.get(targetTabId!);
      if (!currentTab.url) throw new Error("Select an application for this tab before filling");
      let currentPageUrl: string;
      try {
        const currentUrl = new URL(currentTab.url);
        if (currentUrl.protocol !== "http:" && currentUrl.protocol !== "https:") {
          throw new Error("unsupported protocol");
        }
        currentPageUrl = safeResolutionUrl(currentUrl);
      } catch {
        throw new Error("Select an application for this tab before filling");
      }
      if (currentPageUrl !== context.boundPageUrl) {
        throw new Error("Select an application for this tab before filling");
      }
      // This deliberately happens after the live URL guard and immediately before
      // dispatch: a preview-time profile read cannot authorize a later DOM write.
      await apiRequest("/extension/profile/autofill");
      const result = await chrome.tabs.sendMessage(targetTabId!, { type: "EXECUTE_FILL", payload: { mappings: msg.payload.mappings } }) as import("../shared/types").FillExecutionResult;
      if (result.attempted !== result.filled + result.failed || result.attempted + result.skipped !== msg.payload.mappings.length) {
        throw new Error("Autofill execution returned inconsistent counts");
      }
      await apiRequest("/extension/forms/fill-log", {
        method: "POST",
        body: JSON.stringify({
          fields: msg.payload.fields,
          mappings: msg.payload.mappings,
          attempt_id: context.attemptId,
          event_id: crypto.randomUUID(),
          execution: result,
        }),
      });
      return {
        type: "AUTOFILL_COMPLETE",
        payload: result,
      };
    }

    // ── Confirmation Detection ───────────────────────────────────────
    case "CONFIRMATION_DETECTED": {
      const context = await currentBoundContentContext(tabId, msg.payload.pageUrl);
      // Auto-mark as applied when confirmation is detected
      await apiRequest("/extension/applications/mark-applied", {
        method: "POST",
        body: JSON.stringify({
          job_url: msg.payload.pageUrl,
          confirmation_source: "confirmation_detected",
          attempt_id: context.attemptId,
          event_id: msg.payload.eventId,
        }),
      });
      return { type: "MARK_APPLIED_RESULT", payload: { success: true } };
    }

    case "SUBMIT_CLICKED": {
      const context = await currentBoundContentContext(tabId, msg.payload.pageUrl);
      // Record submit_clicked, then prompt user for confirmation
      await apiRequest("/extension/applications/events", {
        method: "POST",
        body: JSON.stringify({
          event_type: "submission_unconfirmed",
          page_url: msg.payload.pageUrl,
          attempt_id: context.attemptId,
          event_id: crypto.randomUUID(),
        }),
      });
      // Trigger the confirmation prompt in the popup
      // This will be handled by the popup's message listener
      chrome.runtime.sendMessage({
        type: "SHOW_CONFIRMATION_PROMPT",
        payload: { pageUrl: msg.payload.pageUrl },
      } satisfies ExtensionMessage).catch(() => {
        // Popup might not be open — that's okay
      });
      return { type: "EVENT_RECORDED", payload: { application_id: "", job_id: 0, status: "saved", event_recorded: true } };
    }

    // ── Content Script → Popup Forwarding ────────────────────────────
    case "JOB_DETECTED": {
      // Persist detected job so popup can show it even after close/reopen
      if (tabId !== undefined) await chrome.storage.session.set({ [`detected_job:${tabId}`]: msg.payload });
      // Forward to popup if open
      chrome.runtime.sendMessage(msg).catch(() => {});
      // Also record the detection event
      apiRequest("/extension/applications/events", {
        method: "POST",
        body: JSON.stringify({
          event_type: EVENT_TYPES.JOB_DETECTED,
          page_url: msg.payload.sourceUrl,
          metadata: { title: msg.payload.title, company: msg.payload.company },
        }),
      }).catch(() => {});
      return { type: "EVENT_RECORDED", payload: { application_id: "", job_id: 0, status: "saved", event_recorded: true } };
    }

    case "GET_TAB_CONTEXT": {
      const targetTabId = await activeTabId();
      if (targetTabId === undefined) return { type: "TAB_CONTEXT", payload: { context: null } };
      return { type: "TAB_CONTEXT", payload: { context: await getAttemptContext(targetTabId), fields: await getDetectedFields(targetTabId) as import("../shared/types").DetectedField[] | undefined } };
    }

    case "RESOLVE_ATTEMPTS": {
      const data = await apiRequest<{ candidates: Array<{ attempt_id: string; application_id: number; role: string; company: string | null; expires_at: string; progress: string }> }>(`/extension/applications/attempts?url=${encodeURIComponent(safeResolutionUrl(new URL(msg.payload.pageUrl)))}`);
      return { type: "ATTEMPT_CANDIDATES", payload: { candidates: data.candidates.map((item) => ({ attemptId: item.attempt_id, applicationId: item.application_id, role: item.role, company: item.company ?? undefined, expiresAt: item.expires_at, progress: item.progress })) } };
    }

    case "BIND_ATTEMPT": {
      const targetTabId = await activeTabId();
      if (targetTabId === undefined) throw new Error("No active browser tab");
      const value = msg.payload;
      const tab = await chrome.tabs.get(targetTabId);
      const boundUrl = tab.url;
      if (!boundUrl) throw new Error("Current browser tab has no URL");
      const pageUrl = safeResolutionUrl(new URL(boundUrl));
      await apiRequest("/extension/applications/events", {
        method: "POST",
        body: JSON.stringify({
          event_type: "opened",
          attempt_id: value.attemptId,
          event_id: crypto.randomUUID(),
          page_url: pageUrl,
        }),
      });
      const currentTab = await chrome.tabs.get(targetTabId);
      if (currentTab.url !== boundUrl) {
        return { type: "TAB_CONTEXT", payload: { context: await getAttemptContext(targetTabId) } };
      }
      const host = new URL(boundUrl).host;
      await setAttemptContext(targetTabId, {
        attemptId: value.attemptId,
        applicationId: value.applicationId,
        jobId: 0,
        expiresAt: value.expiresAt,
        host,
        bindingState: "bound",
        boundPageUrl: pageUrl,
      });
      return { type: "TAB_CONTEXT", payload: { context: await getAttemptContext(targetTabId) } };
    }

    case "CLEAR_TAB_CONTEXT": {
      const targetTabId = await activeTabId();
      if (targetTabId !== undefined) await clearTabContext(targetTabId);
      return { type: "TAB_CONTEXT", payload: { context: null } };
    }

    case "PAGE_CONTEXT": {
      if (tabId === undefined) return { type: "TAB_CONTEXT", payload: { context: null } };
      const context = await reconcileNavigation(tabId, msg.payload.pageUrl);
      return { type: "TAB_CONTEXT", payload: { context } };
    }

    default:
      return { type: "ERROR", payload: { message: `Unknown message type: ${(msg as ExtensionMessage).type}` } };
  }
}

// Keep service worker alive during active operations
// (Manifest V3 service workers may be terminated when idle.)
console.log("[CareerOS] Service worker started");
