// ── LinkedIn Easy Apply Adapter ────────────────────────────────────────────
// Detects the LinkedIn Easy Apply modal, scans its multi-step form fields,
// and fills safe fields. Does NOT auto-submit — the user must review and
// click the final submit button manually.

import type { DetectedField } from "../shared/types";
import { sendMessage } from "../shared/messages";
import { scanForm } from "./form-scanner";
import { fillField } from "./form-filler";
import {
  SAFE_CONFIDENCE_THRESHOLD,
  SENSITIVE_LABEL_PATTERNS,
  NEVER_FILL_INPUT_TYPES,
} from "../shared/constants";

// LinkedIn Easy Apply modal selectors
const EASY_APPLY_MODAL_SELECTOR =
  ".jobs-easy-apply-modal, .jobs-easy-apply-content, [data-test-modal], #artdeco-modal-outlet";
const EASY_APPLY_NEXT_BUTTON = "button[aria-label='Next'], button[aria-label='Continue to next step']";
const EASY_APPLY_REVIEW_BUTTON = "button[aria-label='Review'], button[aria-label='Review your application']";
const EASY_APPLY_SUBMIT_BUTTON = "button[aria-label='Submit application'], button[aria-label='Submit']";
const EASY_APPLY_DISMISS_BUTTON = "button[aria-label='Dismiss'], button[aria-label='Close']";

let easyApplyObserver: MutationObserver | null = null;
let easyApplySteps: DetectedField[][] = [];
let currentStepIndex = 0;

/**
 * Initialize LinkedIn Easy Apply detection.
 * Watches for the Easy Apply modal to appear and scans each step's fields.
 */
export function initLinkedInEasyApply(): void {
  const observer = new MutationObserver(() => {
    const modal = document.querySelector(EASY_APPLY_MODAL_SELECTOR);
    if (modal) {
      onEasyApplyOpened(modal as HTMLElement);
    } else {
      onEasyApplyClosed();
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });

  easyApplyObserver = observer;

  // Also check immediately in case modal is already open
  const existingModal = document.querySelector(EASY_APPLY_MODAL_SELECTOR);
  if (existingModal) {
    onEasyApplyOpened(existingModal as HTMLElement);
  }

  // Listen for next/review button clicks to track step progression
  document.addEventListener("click", handleEasyApplyNavigation, { passive: true });
}

function onEasyApplyOpened(modal: HTMLElement): void {
  // Scan all form fields in the modal
  const fields = scanFormInContainer(modal);

  if (fields.length === 0) return;

  // Send form detection event
  sendMessage({
    type: "FORM_DETECTED",
    payload: {
      fields,
      domain: window.location.hostname,
      atsType: "linkedin_easy_apply",
    },
  }).catch(() => {});

  // Store fields for this step
  easyApplySteps[currentStepIndex] = fields;
}

function onEasyApplyClosed(): void {
  easyApplySteps = [];
  currentStepIndex = 0;
}

function handleEasyApplyNavigation(event: MouseEvent): void {
  const target = event.target as HTMLElement;
  const button = target.closest("button") as HTMLButtonElement | null;
  if (!button) return;

  const label = button.getAttribute("aria-label") ?? "";

  // Track next/continue clicks to advance step index
  if (
    label.includes("Next") ||
    label.includes("Continue") ||
    label.includes("Review")
  ) {
    currentStepIndex++;
  }

  // Never auto-submit — just record that submit was clicked
  if (label.includes("Submit")) {
    sendMessage({
      type: "SUBMIT_CLICKED",
      payload: { pageUrl: window.location.href },
    }).catch(() => {});
  }
}

/**
 * Scan form fields within a specific container (the Easy Apply modal).
 */
function scanFormInContainer(container: HTMLElement): DetectedField[] {
  const fields: DetectedField[] = [];
  const seen = new Set<string>();

  const elements = container.querySelectorAll(
    "input:not([type='hidden']), textarea, select, [contenteditable='true']"
  );

  for (const el of elements) {
    if (!(el instanceof HTMLElement)) continue;

    // Skip hidden elements
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;

    const tagName = el.tagName.toLowerCase();
    const label = findEasyApplyLabel(el);
    const labelText = label?.textContent?.trim();
    const placeholder = el.getAttribute("placeholder") ?? undefined;
    const ariaLabel = el.getAttribute("aria-label") ?? undefined;
    const autocomplete = el.getAttribute("autocomplete") ?? undefined;
    const required =
      el.getAttribute("required") !== null ||
      el.getAttribute("aria-required") === "true";

    let inputType: string | undefined;
    if (el instanceof HTMLInputElement) {
      inputType = el.type || "text";
    } else if (el instanceof HTMLSelectElement) {
      inputType = "select";
    } else if (el instanceof HTMLTextAreaElement) {
      inputType = "textarea";
    } else if (el.getAttribute("contenteditable") === "true") {
      inputType = "richtext";
    }

    const fieldId = el.id || el.getAttribute("name") || `field-${Math.random().toString(36).slice(2, 8)}`;
    if (seen.has(fieldId)) continue;
    seen.add(fieldId);

    // Skip password and file inputs
    if (inputType && NEVER_FILL_INPUT_TYPES.has(inputType)) continue;

    // Skip sensitive fields
    const combinedLabel = `${labelText ?? ""} ${placeholder ?? ""} ${ariaLabel ?? ""}`.toLowerCase();
    if (SENSITIVE_LABEL_PATTERNS.some((p) => p.test(combinedLabel))) continue;

    fields.push({
      elementId: fieldId,
      tagName,
      inputType,
      name: el.getAttribute("name") ?? undefined,
      id: el.id || undefined,
      label: labelText,
      placeholder,
      ariaLabel,
      autocomplete,
      options: undefined,
      isRequired: required,
      visible: true,
      selectorPath: buildEasyApplySelector(el),
    });
  }

  return fields;
}

/**
 * Find the label for a field in the Easy Apply modal.
 * LinkedIn uses a specific label structure.
 */
function findEasyApplyLabel(el: HTMLElement): HTMLLabelElement | null {
  // Check for aria-labelledby
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const label = document.getElementById(labelledBy);
    if (label) return label as HTMLLabelElement;
  }

  // Check for explicit label[for]
  if (el.id) {
    const label = document.querySelector<HTMLLabelElement>(`label[for="${el.id}"]`);
    if (label) return label;
  }

  // Walk up to find label in the same container
  let parent = el.parentElement;
  for (let i = 0; i < 5 && parent; i++) {
    const label = parent.querySelector("label");
    if (label) return label;

    // LinkedIn sometimes wraps in a div with a span as label
    const span = parent.querySelector("span.jobs-easy-apply-form-element__label, span.artdeco-text-input--label");
    if (span) return span as unknown as HTMLLabelElement;

    parent = parent.parentElement;
  }

  return null;
}

/**
 * Build a selector path for fields inside the Easy Apply modal.
 */
function buildEasyApplySelector(el: HTMLElement): string {
  if (el.id) return `#${el.id}`;
  if (el.getAttribute("name")) return `[name="${el.getAttribute("name")}"]`;

  const parts: string[] = [];
  let current: HTMLElement | null = el;
  while (current && current !== document.body && !current.matches(EASY_APPLY_MODAL_SELECTOR)) {
    let selector = current.tagName.toLowerCase();
    if (current.className && typeof current.className === "string") {
      const classes = current.className.trim().split(/\s+/).slice(0, 2).join(".");
      if (classes) selector += `.${classes}`;
    }
    const parent: HTMLElement | null = current.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(
        (c: Element) => c.tagName === current!.tagName,
      );
      if (siblings.length > 1) {
        const index = siblings.indexOf(current) + 1;
        selector += `:nth-child(${index})`;
      }
    }
    parts.unshift(selector);
    current = parent;
  }
  return parts.join(" > ");
}

/**
 * Fill all current-step fields that are mapped with high confidence.
 * Called from the popup when the user clicks "Fill Safe Fields".
 */
export function fillEasyApplyFields(
  mappings: Array<{
    elementId: string;
    value?: string;
    action: "fill" | "review" | "skip";
  }>,
): { filled: number; skipped: number } {
  let filled = 0;
  let skipped = 0;

  for (const mapping of mappings) {
    if (mapping.action !== "fill" || !mapping.value) {
      skipped++;
      continue;
    }

    const modal = document.querySelector(EASY_APPLY_MODAL_SELECTOR);
    if (!modal) continue;

    // Try to find the field within the modal
    const el =
      (document.getElementById(mapping.elementId) as HTMLElement | null) ??
      (modal.querySelector(`#${mapping.elementId}`) as HTMLElement | null) ??
      (modal.querySelector(`[name="${mapping.elementId}"]`) as HTMLElement | null) ??
      modal.querySelector(mapping.elementId) as HTMLElement | null;

    if (!el || !(el instanceof HTMLElement)) {
      skipped++;
      continue;
    }

    const field: DetectedField = {
      elementId: mapping.elementId,
      tagName: el.tagName.toLowerCase(),
      inputType: el instanceof HTMLInputElement ? el.type : undefined,
      name: el.getAttribute("name") ?? undefined,
      id: el.id,
      label: findEasyApplyLabel(el)?.textContent?.trim(),
      visible: true,
      selectorPath: buildEasyApplySelector(el),
    };

    const success = fillField(field, mapping.value);
    if (success) {
      filled++;
    } else {
      skipped++;
    }
  }

  return { filled, skipped };
}

/**
 * Check if the Easy Apply modal is currently open.
 */
export function isEasyApplyOpen(): boolean {
  return document.querySelector(EASY_APPLY_MODAL_SELECTOR) !== null;
}
