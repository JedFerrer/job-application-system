// ── Submit Detector ────────────────────────────────────────────────────────
// Detects submit button clicks and confirmation messages on ATS forms.
// When a submit is detected, scans for confirmation text.
// If confirmation found → CONFIRMATION_DETECTED message.
// If uncertain → SUBMIT_CLICKED message → service worker asks user.

import { sendMessage } from "../shared/messages";
import { SUBMIT_BUTTON_PATTERNS, CONFIRMATION_PATTERNS } from "../shared/constants";

const INITIAL_CONFIRMATION_DELAY_MS = 3_000;
const LATE_CONFIRMATION_WINDOW_MS = 30_000;

type PendingSubmitAttempt = {
  pageUrl: string;
  initialTimer: ReturnType<typeof setTimeout>;
  observer: MutationObserver | null;
  expiryTimer: ReturnType<typeof setTimeout> | null;
  unconfirmedEmitted: boolean;
  confirmationEmitted: boolean;
  confirmationEventId: string;
};

let pendingSubmit: PendingSubmitAttempt | null = null;
let pageLoadConfirmationEmitted = false;

export function initSubmitDetector(): void {
  // Detect submit button clicks
  document.addEventListener("click", handleSubmitClick, { passive: true });

  // After a submit is detected, watch for confirmation messages
  if (document.readyState === "complete") {
    checkConfirmation();
  } else {
    window.addEventListener("load", checkConfirmation);
  }

  window.addEventListener("pagehide", clearPendingSubmit);
  window.addEventListener("unload", clearPendingSubmit);
}

function handleSubmitClick(event: MouseEvent): void {
  const target = event.target as HTMLElement;
  const button = target.closest("button, input[type='submit'], a[role='button']") as HTMLElement | null;
  if (!button) return;

  const text = (button.textContent ?? (button as HTMLInputElement).value ?? "").toLowerCase().trim();
  const isSubmit = SUBMIT_BUTTON_PATTERNS.some((p) => p.test(text));

  if (!isSubmit) return;

  clearPendingSubmit();
  const attempt: PendingSubmitAttempt = {
    pageUrl: window.location.href,
    initialTimer: setTimeout(() => handleInitialConfirmationCheck(attempt), INITIAL_CONFIRMATION_DELAY_MS),
    observer: null,
    expiryTimer: null,
    unconfirmedEmitted: false,
    confirmationEmitted: false,
    confirmationEventId: crypto.randomUUID(),
  };
  pendingSubmit = attempt;
}

function handleInitialConfirmationCheck(attempt: PendingSubmitAttempt): void {
  if (!isCurrentAttempt(attempt)) return;

  const confirmed = scanForConfirmation();
  if (confirmed) {
    emitAttemptConfirmation(attempt, confirmed);
    return;
  }

  // Observe first, then scan again. This closes the gap where a success node
  // could be inserted after the initial scan but before observation begins.
  const observer = new MutationObserver(() => {
    if (!isCurrentAttempt(attempt)) return;
    const lateConfirmation = scanForConfirmation();
    if (lateConfirmation) emitAttemptConfirmation(attempt, lateConfirmation);
  });
  observer.observe(document, { childList: true, subtree: true });
  attempt.observer = observer;

  const confirmationAfterObserve = scanForConfirmation();
  if (confirmationAfterObserve) {
    emitAttemptConfirmation(attempt, confirmationAfterObserve);
    return;
  }

  if (!attempt.unconfirmedEmitted) {
    attempt.unconfirmedEmitted = true;
    sendMessage({
      type: "SUBMIT_CLICKED",
      payload: { pageUrl: attempt.pageUrl },
    }).catch(() => {});
  }
  attempt.expiryTimer = setTimeout(() => {
    if (pendingSubmit === attempt) clearPendingSubmit();
  }, LATE_CONFIRMATION_WINDOW_MS);
}

function isCurrentAttempt(attempt: PendingSubmitAttempt): boolean {
  if (pendingSubmit !== attempt) return false;
  if (window.location.href === attempt.pageUrl) return true;
  clearPendingSubmit();
  return false;
}

function emitAttemptConfirmation(attempt: PendingSubmitAttempt, confirmationText: string): void {
  if (!isCurrentAttempt(attempt) || attempt.confirmationEmitted) return;
  attempt.confirmationEmitted = true;
  sendMessage({
    type: "CONFIRMATION_DETECTED",
    payload: {
      pageUrl: attempt.pageUrl,
      confirmationText,
      eventId: attempt.confirmationEventId,
    },
  }).catch(() => {});
  clearPendingSubmit();
}

function clearPendingSubmit(): void {
  if (!pendingSubmit) return;
  clearTimeout(pendingSubmit.initialTimer);
  if (pendingSubmit.expiryTimer) clearTimeout(pendingSubmit.expiryTimer);
  pendingSubmit.observer?.disconnect();
  pendingSubmit = null;
}

function checkConfirmation(): void {
  const confirmed = scanForConfirmation();
  if (!confirmed) return;
  if (pendingSubmit && isCurrentAttempt(pendingSubmit)) {
    emitAttemptConfirmation(pendingSubmit, confirmed);
    return;
  }
  if (!pageLoadConfirmationEmitted) {
    pageLoadConfirmationEmitted = true;
    sendMessage({
      type: "CONFIRMATION_DETECTED",
      payload: {
        pageUrl: window.location.href,
        confirmationText: confirmed,
        eventId: crypto.randomUUID(),
      },
    }).catch(() => {});
  }
}

/**
 * Scan the current page for confirmation messages.
 * Returns the matched confirmation text, or null.
 */
export function scanForConfirmation(): string | null {
  // Look in common containers
  const containers = document.querySelectorAll(
    "h1, h2, h3, .alert, .success, .confirmation, [class*='success'], [class*='thank'], [class*='confirm'], [role='alert']"
  );

  for (const el of containers) {
    const text = (el.textContent ?? "").trim();
    for (const pattern of CONFIRMATION_PATTERNS) {
      const match = text.match(pattern);
      if (match) {
        return match[0];
      }
    }
  }

  // Also check the full page body for confirmation (when page content is minimal)
  const bodyText = (document.body.innerText ?? document.body.textContent ?? "").slice(0, 1000);
  for (const pattern of CONFIRMATION_PATTERNS) {
    const match = bodyText.match(pattern);
    if (match) {
      return match[0];
    }
  }

  return null;
}
