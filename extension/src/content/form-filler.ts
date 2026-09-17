// ── Form Filler ────────────────────────────────────────────────────────────
// Utilities for programmatically filling form fields while triggering
// the appropriate events so React/Angular/Vue controlled components
// register the changes.

import type { DetectedField, FieldMapping } from "../shared/types";
import { NEVER_FILL_INPUT_TYPES, SENSITIVE_LABEL_PATTERNS } from "../shared/constants";
import { EXECUTABLE_SAFE_PROFILE_PATHS } from "../shared/autofill-policy";

/**
 * Fill a single field with a value, dispatching native events.
 * Returns true if the field was filled.
 */
export function fillField(
  field: DetectedField,
  value: string,
): boolean {
  // Safety: never fill password or file inputs
  if (field.inputType && NEVER_FILL_INPUT_TYPES.has(field.inputType)) {
    return false;
  }

  // Safety: never fill sensitive fields
  const labelLower = (field.label ?? "").toLowerCase();
  const placeholderLower = (field.placeholder ?? "").toLowerCase();
  const combinedText = `${labelLower} ${placeholderLower}`;
  if (SENSITIVE_LABEL_PATTERNS.some((p) => p.test(combinedText))) {
    return false;
  }

  const el = resolveElement(field);
  if (!el) return false;

  if (el instanceof HTMLInputElement) {
    return fillInput(el, value);
  } else if (el instanceof HTMLTextAreaElement) {
    return fillTextarea(el, value);
  } else if (el instanceof HTMLSelectElement) {
    return fillSelect(el, value);
  } else if (el.getAttribute("contenteditable") === "true") {
    return fillContentEditable(el, value);
  }

  return false;
}

/**
 * Fill multiple fields from mappings.
 */
export function fillFields(mappings: FieldMapping[]): { attempted: number; filled: number; skipped: number; failed: number } {
  let attempted = 0;
  let filled = 0;
  let skipped = 0;
  let failed = 0;

  for (const mapping of mappings) {
    if (mapping.action !== "fill" || !mapping.value || !mapping.profilePath || !EXECUTABLE_SAFE_PROFILE_PATHS.has(mapping.profilePath)) {
      skipped++;
      continue;
    }

    attempted++;

    // Find the field by elementId (need the full DetectedField)
    // We use the selectorPath for reliable re-location
    const outcome = fillFieldBySelector(mapping.selectorPath ?? "", mapping.value);
    if (outcome === true) {
      filled++;
    } else if (outcome === null) {
      attempted--;
      skipped++;
    } else {
      failed++;
    }
  }

  return { attempted, filled, skipped, failed };
}

// ── Internal Helpers ───────────────────────────────────────────────────────

function resolveElement(field: DetectedField): HTMLElement | null {
  // Try selector path first (most reliable for re-location)
  if (field.selectorPath) {
    try {
      const el = document.querySelector(field.selectorPath);
      if (el instanceof HTMLElement) return el;
    } catch {
      // Invalid selector — fall through
    }
  }

  // Try by ID
  if (field.id) {
    const el = document.getElementById(field.id);
    if (el) return el;
  }

  // Try by name
  if (field.name) {
    const el = document.querySelector(`[name="${field.name}"]`);
    if (el instanceof HTMLElement) return el;
  }

  // Try by label
  if (field.label) {
    const label = document.querySelector(`label`);
    if (label instanceof HTMLLabelElement && label.textContent?.trim() === field.label) {
      if (label.htmlFor) {
        const el = document.getElementById(label.htmlFor);
        if (el) return el;
      }
      const el = label.querySelector("input, textarea, select");
      if (el instanceof HTMLElement) return el;
    }
  }

  return null;
}

function fillFieldBySelector(selectorPath: string, value: string): boolean | null {
  if (!selectorPath) return false;
  try {
    const el = document.querySelector(selectorPath);
    if (!(el instanceof HTMLElement)) return false;

    // Safety: never fill password or file inputs (re-check at fill-time)
    if (el instanceof HTMLInputElement) {
      const inputType = (el.type || "text").toLowerCase();
      if (NEVER_FILL_INPUT_TYPES.has(inputType)) return null;
    }

    // Safety: never fill fields with sensitive labels (re-check at fill-time)
    if (SENSITIVE_LABEL_PATTERNS.some((p) => p.test(liveLabelText(el)))) return null;

    if (el instanceof HTMLInputElement) return fillInput(el, value);
    if (el instanceof HTMLTextAreaElement) return fillTextarea(el, value);
    if (el instanceof HTMLSelectElement) return fillSelect(el, value);
  } catch {
    return false;
  }
  return false;
}

function liveLabelText(el: HTMLElement): string {
  const labels = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement
    ? Array.from(el.labels ?? []).map((label) => label.textContent ?? "") : [];
  const labelledBy = (el.getAttribute("aria-labelledby") ?? "").split(/\s+/).filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "");
  const wrapping = el.closest("label")?.textContent ?? "";
  return [
    ...labels,
    ...labelledBy,
    wrapping,
    el.getAttribute("aria-label") ?? "",
    el.getAttribute("placeholder") ?? "",
  ].join(" ").toLowerCase();
}

function fillInput(input: HTMLInputElement, value: string): boolean {
  // Use native setter + event dispatch for React/Angular compatibility
  const nativeSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;

  if (nativeSetter) {
    nativeSetter.call(input, value);
  } else {
    input.value = value;
  }

  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  // Some frameworks listen for blur
  input.dispatchEvent(new FocusEvent("focus", { bubbles: true }));
  input.dispatchEvent(new FocusEvent("blur", { bubbles: true }));

  return true;
}

function fillTextarea(textarea: HTMLTextAreaElement, value: string): boolean {
  const nativeSetter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    "value",
  )?.set;

  if (nativeSetter) {
    nativeSetter.call(textarea, value);
  } else {
    textarea.value = value;
  }

  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.dispatchEvent(new Event("change", { bubbles: true }));

  return true;
}

function fillSelect(select: HTMLSelectElement, value: string): boolean {
  // Try to match by value first
  select.value = value;
  if (select.value === value) {
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  // Try to match by option text
  for (const option of select.options) {
    if (option.textContent?.trim().toLowerCase() === value.toLowerCase()) {
      select.value = option.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
  }

  return false;
}

function fillContentEditable(el: HTMLElement, value: string): boolean {
  el.textContent = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}
