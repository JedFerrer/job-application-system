// ── Submit Detector ────────────────────────────────────────────────────────
// Detects submit button clicks and confirmation messages on ATS forms.
// When a submit is detected, scans for confirmation text.
// If confirmation found → CONFIRMATION_DETECTED message.
// If uncertain → SUBMIT_CLICKED message → service worker asks user.

import { sendMessage } from "../shared/messages";
import { SUBMIT_BUTTON_PATTERNS, CONFIRMATION_PATTERNS } from "../shared/constants";

let submitClicked = false;

export function initSubmitDetector(): void {
  // Detect submit button clicks
  document.addEventListener("click", handleSubmitClick, { passive: true });

  // After a submit is detected, watch for confirmation messages
  if (document.readyState === "complete") {
    checkConfirmation();
  } else {
    window.addEventListener("load", checkConfirmation);
  }
}

function handleSubmitClick(event: MouseEvent): void {
  const target = event.target as HTMLElement;
  const button = target.closest("button, input[type='submit'], a[role='button']") as HTMLElement | null;
  if (!button) return;

  const text = (button.textContent ?? (button as HTMLInputElement).value ?? "").toLowerCase().trim();
  const isSubmit = SUBMIT_BUTTON_PATTERNS.some((p) => p.test(text));

  if (!isSubmit) return;

  submitClicked = true;

  // Don't send immediately — wait to see if confirmation appears
  // (page may navigate or show a success message)
  setTimeout(() => {
    if (!submitClicked) return; // Already handled by confirmation detection

    // Check for confirmation text on the current page
    const confirmed = scanForConfirmation();
    if (confirmed) {
      sendMessage({
        type: "CONFIRMATION_DETECTED",
        payload: {
          pageUrl: window.location.href,
          confirmationText: confirmed,
        },
      }).catch(() => {});
    } else {
      // No confirmation detected — ask user
      sendMessage({
        type: "SUBMIT_CLICKED",
        payload: { pageUrl: window.location.href },
      }).catch(() => {});
    }
  }, 3000); // Wait 3 seconds for page to update/redirect
}

function checkConfirmation(): void {
  const confirmed = scanForConfirmation();
  if (confirmed) {
    submitClicked = false; // Cancel pending submit prompt
    sendMessage({
      type: "CONFIRMATION_DETECTED",
      payload: {
        pageUrl: window.location.href,
        confirmationText: confirmed,
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
  const bodyText = document.body.innerText.slice(0, 1000);
  for (const pattern of CONFIRMATION_PATTERNS) {
    const match = bodyText.match(pattern);
    if (match) {
      return match[0];
    }
  }

  return null;
}
