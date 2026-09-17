// ── Content Script Entry Point ─────────────────────────────────────────────
// Runs on supported job board and ATS pages (see manifest.json matches).
// Orchestrates job detection, extraction, apply-click tracking, form scanning,
// and submit/confirmation detection.

import { sendMessage } from "../shared/messages";
import { isJobUrl, detectAtsType, hashHtml } from "../shared/validators";
import { detectJob } from "./job-detector";
import { extractJob } from "./job-extractor";
import { initApplyClickTracker } from "./apply-click-tracker";
import { scanForm } from "./form-scanner";
import { initSubmitDetector } from "./submit-detector";
import { initLinkedInEasyApply } from "./linkedin-easy-apply";
import { fillFields } from "./form-filler";

let initialized = false;
let lastDetectedFieldsFingerprint: string | null = null;

function init(): void {
  if (initialized) return;
  initialized = true;

  const currentUrl = window.location.href;
  sendMessage({ type: "PAGE_CONTEXT", payload: { pageUrl: currentUrl } }).catch(() => {});

  // 1. Always track Apply button clicks
  initApplyClickTracker();

  // 2. Always detect submit clicks and confirmations
  initSubmitDetector();

  // 3. LinkedIn Easy Apply form detection (only on LinkedIn)
  if (window.location.hostname.includes("linkedin.com")) {
    initLinkedInEasyApply();
  }

  // 4. If this looks like a job page, detect and extract
  if (isJobUrl(currentUrl)) {
    const job = detectAndExtract();
    if (job && job.title && job.company) {
      sendMessage({ type: "JOB_DETECTED", payload: job as import("../shared/types").ExtractedJob }).catch(() => {
        // Service worker may not be ready yet — retry on next page action
      });
    }
  }

  // 5. If this looks like an application form page, scan it
  // (We check after a short delay for dynamic forms to render)
  setTimeout(() => {
    reportDetectedFields(scanForm(), true);
  }, 1500);

  // 6. Watch for dynamic content changes (SPA navigation, modal forms)
  initMutationObserver();
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "EXECUTE_FILL") {
    try {
      sendResponse(fillFields(message.payload?.mappings ?? []));
    } catch {
      sendResponse({ attempted: 0, filled: 0, skipped: 0, failed: 1 });
    }
    return true;
  }
  return false;
});

function detectAndExtract() {
  try {
    const job = detectJob();
    if (!job) return null;

    const extracted = extractJob(job);
    if (!extracted || !extracted.title || !extracted.company) return null;

    return {
      ...extracted,
      sourceUrl: window.location.href,
      source: detectAtsType(window.location.href) ?? "generic",
      rawHtmlHash: hashHtml(document.body.innerText),
      extractedAt: new Date().toISOString(),
      atsType: detectAtsType(window.location.href) as
        | "linkedin"
        | "indeed"
        | "greenhouse"
        | "lever"
        | "workday"
        | "generic"
        | undefined,
    };
  } catch {
    return null;
  }
}

function initMutationObserver(): void {
  const observer = new MutationObserver(() => {
    // Re-scan form on significant DOM changes (debounced)
    clearTimeout((observer as unknown as { _timer?: ReturnType<typeof setTimeout> })._timer);
    (observer as unknown as { _timer?: ReturnType<typeof setTimeout> })._timer = setTimeout(() => {
      reportDetectedFields(scanForm(), false);
    }, 2000);
  });

  observer.observe(document, {
    childList: true,
    subtree: true,
    attributes: false,
  });

  // The worker owns tab-scoped persistence; content scripts must not write
  // global detected-field keys that can leak state across tabs.
}

function reportDetectedFields(fields: import("../shared/types").DetectedField[], initial: boolean): void {
  const fingerprint = JSON.stringify(fields);
  if (fingerprint === lastDetectedFieldsFingerprint) return;
  const previouslyDetected = lastDetectedFieldsFingerprint !== null && lastDetectedFieldsFingerprint !== "[]";
  lastDetectedFieldsFingerprint = fingerprint;
  if (fields.length === 0 && (initial || !previouslyDetected)) return;
  sendMessage({
    type: "FORM_DETECTED",
    payload: {
      fields,
      domain: window.location.hostname,
      atsType: detectAtsType(window.location.href),
      pageUrl: window.location.href,
    },
  }).catch(() => {});
}

// ── Bootstrap ──────────────────────────────────────────────────────────────

// Run on DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
