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

let initialized = false;

function init(): void {
  if (initialized) return;
  initialized = true;

  const currentUrl = window.location.href;

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
    const fields = scanForm();
    if (fields.length > 0) {
      const atsType = detectAtsType(currentUrl);
      sendMessage({
        type: "FORM_DETECTED",
        payload: {
          fields,
          domain: window.location.hostname,
          atsType,
          pageUrl: window.location.href,
        },
      }).catch(() => {});
    }
  }, 1500);

  // 6. Watch for dynamic content changes (SPA navigation, modal forms)
  initMutationObserver();
}

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
      const fields = scanForm();
      if (fields.length > 0) {
        sendMessage({
          type: "FORM_DETECTED",
          payload: {
            fields,
            domain: window.location.hostname,
            atsType: detectAtsType(window.location.href),
          },
        }).catch(() => {});
      }
    }, 2000);
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: false,
  });

  // Also persist form fields immediately so popup can show them on reopen
  const fields = scanForm();
  if (fields.length > 0) {
    chrome.storage.session.set({
      detected_fields: fields,
      detected_fields_domain: window.location.hostname,
      detected_fields_ats_type: detectAtsType(window.location.href),
    }).catch(() => {});
  }
}

// ── Bootstrap ──────────────────────────────────────────────────────────────

// Run on DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
