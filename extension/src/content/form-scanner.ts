// ── Form Scanner ───────────────────────────────────────────────────────────
// Scans the current page for form fields: input, textarea, select, radio,
// checkbox, and contenteditable elements. Returns normalized DetectedField[].

import type { DetectedField } from "../shared/types";
import { getElementId, findLabel, isVisible, getSelectorPath } from "./dom-utils";

/**
 * Scan all form fields on the page.
 */
export function scanForm(): DetectedField[] {
  const fields: DetectedField[] = [];
  const seen = new Set<string>();

  // Scan standard form elements
  const elements = document.querySelectorAll(
    "input:not([type='hidden']), textarea, select, [contenteditable='true']"
  );

  for (const el of elements) {
    if (!(el instanceof HTMLElement)) continue;

    const field = buildDetectedField(el);
    if (!field) continue;

    // Deduplicate by element ID
    if (seen.has(field.elementId)) continue;
    seen.add(field.elementId);

    // Skip password and file inputs
    if (field.inputType === "password" || field.inputType === "file") continue;

    fields.push(field);
  }

  // Also scan radio buttons (they're often in groups)
  const radioGroups = new Map<string, HTMLInputElement[]>();
  const radios = document.querySelectorAll<HTMLInputElement>("input[type='radio']");
  for (const radio of radios) {
    if (!isVisible(radio)) continue;
    const name = radio.getAttribute("name") ?? radio.id ?? "radio-unknown";
    if (!radioGroups.has(name)) radioGroups.set(name, []);
    radioGroups.get(name)!.push(radio);
  }

  for (const [name, group] of radioGroups) {
    const firstRadio = group[0];
    if (!firstRadio) continue;
    const field = buildDetectedField(firstRadio);
    if (!field || seen.has(field.elementId)) continue;
    seen.add(field.elementId);

    // Collect radio options
    const options: string[] = [];
    for (const radio of group) {
      const label = findLabel(radio);
      const optionText = label?.textContent?.trim() ?? radio.value ?? radio.id ?? "";
      if (optionText) options.push(optionText);
    }
    field.options = options;
    fields.push(field);
  }

  return fields;
}

/**
 * Build a DetectedField from a DOM element.
 */
function buildDetectedField(el: HTMLElement): DetectedField | null {
  if (!isVisible(el)) return null;

  const tagName = el.tagName.toLowerCase();
  const label = findLabel(el);
  const labelText = label?.textContent?.trim();
  const placeholder = el.getAttribute("placeholder") ?? undefined;
  const ariaLabel = el.getAttribute("aria-label") ?? undefined;
  const autocomplete = el.getAttribute("autocomplete") ?? undefined;
  const required = el.getAttribute("required") !== null || el.getAttribute("aria-required") === "true";

  let inputType: string | undefined;
  let options: string[] | undefined;

  if (el instanceof HTMLInputElement) {
    inputType = el.type || "text";
  } else if (el instanceof HTMLSelectElement) {
    inputType = "select";
    options = Array.from(el.options)
      .filter((o) => o.value)
      .map((o) => o.textContent?.trim() ?? o.value);
  } else if (el instanceof HTMLTextAreaElement) {
    inputType = "textarea";
  } else if (el.getAttribute("contenteditable") === "true") {
    inputType = "richtext";
  }

  return {
    elementId: getElementId(el),
    tagName,
    inputType,
    name: el.getAttribute("name") ?? undefined,
    id: el.id || undefined,
    label: labelText,
    placeholder,
    ariaLabel,
    autocomplete,
    options,
    isRequired: required,
    visible: true,
    selectorPath: getSelectorPath(el),
  };
}
