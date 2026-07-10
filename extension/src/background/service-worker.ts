// ── Background Service Worker ──────────────────────────────────────────────
// Message routing hub: receives messages from popup and content scripts,
// calls the backend API, and returns responses. Also handles long-lived
// extension lifecycle (install, startup).

import { onMessage } from "../shared/messages";
import type { ExtensionMessage } from "../shared/types";
import { apiRequest } from "./api-client";
import { completePairing, getSessionState, logout } from "./auth-manager";
import { EVENT_TYPES } from "../shared/constants";

// ── Lifecycle ──────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log("[CareerOS] Extension installed");
});

// ── Message Router ─────────────────────────────────────────────────────────

onMessage((msg, _sender, sendResponse) => {
  handleMessage(msg)
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

async function handleMessage(msg: ExtensionMessage): Promise<ExtensionMessage> {
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
      await apiRequest("/extension/applications/events", {
        method: "POST",
        body: JSON.stringify({
          event_type: EVENT_TYPES.FORM_DETECTED,
          page_url: pageUrl,
          metadata: {
            domain: msg.payload.domain,
            ats_type: msg.payload.atsType,
            field_count: (msg.payload as { fields: unknown[] }).fields.length,
          },
        }),
      });
      // Persist detected fields for popup
      await chrome.storage.session.set({
        detected_fields: (msg.payload as { fields: unknown }).fields,
        detected_fields_domain: msg.payload.domain,
        detected_fields_ats_type: msg.payload.atsType,
      });
      return { type: "EVENT_RECORDED", payload: { application_id: "", job_id: 0, status: "saved", event_recorded: true } };
    }

    // ── Mark as Applied ──────────────────────────────────────────────
    case "MARK_APPLIED": {
      await apiRequest("/extension/applications/mark-applied", {
        method: "POST",
        body: JSON.stringify({
          job_url: msg.payload.jobUrl,
          confirmation_source: "user_marked_applied",
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
      // Log the fill session
      await apiRequest("/extension/forms/fill-log", {
        method: "POST",
        body: JSON.stringify({
          fields: msg.payload.fields,
          mappings: msg.payload.mappings,
        }),
      });
      return {
        type: "AUTOFILL_COMPLETE",
        payload: {
          filled: msg.payload.mappings.filter((m) => m.action === "fill").length,
          skipped: msg.payload.mappings.filter((m) => m.action !== "fill").length,
        },
      };
    }

    // ── Confirmation Detection ───────────────────────────────────────
    case "CONFIRMATION_DETECTED": {
      // Auto-mark as applied when confirmation is detected
      await apiRequest("/extension/applications/mark-applied", {
        method: "POST",
        body: JSON.stringify({
          job_url: msg.payload.pageUrl,
          confirmation_source: "confirmation_detected",
          confirmation_text: msg.payload.confirmationText,
        }),
      });
      return { type: "MARK_APPLIED_RESULT", payload: { success: true } };
    }

    case "SUBMIT_CLICKED": {
      // Record submit_clicked, then prompt user for confirmation
      await apiRequest("/extension/applications/events", {
        method: "POST",
        body: JSON.stringify({
          event_type: EVENT_TYPES.SUBMIT_CLICKED,
          page_url: msg.payload.pageUrl,
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
      await chrome.storage.session.set({ detected_job: msg.payload });
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

    default:
      return { type: "ERROR", payload: { message: `Unknown message type: ${(msg as ExtensionMessage).type}` } };
  }
}

// Keep service worker alive during active operations
// (Manifest V3 service workers may be terminated when idle.)
console.log("[CareerOS] Service worker started");
