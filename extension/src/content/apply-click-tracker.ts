// ── Apply Click Tracker ────────────────────────────────────────────────────
// Uses event delegation on document.click to detect Apply button clicks.
// Sends APPLY_CLICKED message to the service worker.

import { sendMessage } from "../shared/messages";
import { APPLY_BUTTON_PATTERNS } from "../shared/constants";

export function initApplyClickTracker(): void {
  document.addEventListener("click", handleClick, { passive: true });
}

function handleClick(event: MouseEvent): void {
  const target = event.target as HTMLElement;
  const button = target.closest("button, a, [role='button']") as HTMLElement | null;

  if (!button) return;

  const text = (button.textContent ?? "").toLowerCase().trim();
  const aria = (button.getAttribute("aria-label") ?? "").toLowerCase();
  const title = (button.getAttribute("title") ?? "").toLowerCase();
  const combined = `${text} ${aria} ${title}`;

  const isApplyButton = APPLY_BUTTON_PATTERNS.some((p) => p.test(combined));

  if (!isApplyButton) return;

  // Don't send if it looks like a generic "Submit" on a non-job page
  if (text === "submit" && !window.location.href.includes("job") && !window.location.href.includes("apply")) {
    return;
  }

  const href = button instanceof HTMLAnchorElement ? button.href : null;

  sendMessage({
    type: "APPLY_CLICKED",
    payload: {
      pageUrl: window.location.href,
      buttonText: button.textContent?.trim() ?? "",
      href,
    },
  }).catch(() => {
    // Best-effort — service worker may not be ready
  });
}
