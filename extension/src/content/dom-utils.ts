// ── DOM Utility Functions ──────────────────────────────────────────────────

/**
 * Generate a unique, stable element ID for a form field.
 * Uses existing `id`, `name`, or generates one from label text + position.
 */
export function getElementId(el: HTMLElement): string {
  if (el.id) return el.id;
  // Create a stable selector-based ID
  const tag = el.tagName.toLowerCase();
  const name = el.getAttribute("name") ?? "";
  const type = (el as HTMLInputElement).type ?? "";
  const label = findLabel(el);
  const labelText = label?.textContent?.trim().slice(0, 30) ?? "";
  return `${tag}-${type || "text"}-${name}-${labelText}`.replace(/\s+/g, "-").toLowerCase() || `field-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Build a CSS selector path from the element to document root.
 * Used for reliable re-location of elements during fill.
 */
export function getSelectorPath(el: HTMLElement): string {
  const parts: string[] = [];
  let current: HTMLElement | null = el;
  while (current && current !== document.body) {
    let selector = current.tagName.toLowerCase();
    if (current.id) {
      selector = `#${current.id}`;
      parts.unshift(selector);
      break;
    }
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
 * Find the associated `<label>` element for a form field.
 * Checks `for` attribute first, then parent traversal.
 */
export function findLabel(el: HTMLElement): HTMLLabelElement | null {
  // Check for explicit label[for]
  if (el.id) {
    const label = document.querySelector<HTMLLabelElement>(`label[for="${el.id}"]`);
    if (label) return label;
  }
  // Check if wrapped in a label
  let parent = el.parentElement;
  for (let i = 0; i < 5 && parent; i++) {
    if (parent.tagName === "LABEL") return parent as HTMLLabelElement;
    // Some frameworks wrap fields in a container with a label sibling
    const label = parent.querySelector("label");
    if (label) return label;
    parent = parent.parentElement;
  }
  return null;
}

/**
 * Check if an element is visible (not hidden by CSS, not in a hidden container).
 */
export function isVisible(el: HTMLElement): boolean {
  const style = window.getComputedStyle(el);
  if (style.display === "none" || style.visibility === "hidden") return false;
  if (style.opacity === "0") return false;

  // Check dimensions
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return false;

  // Check if any ancestor is hidden
  let parent = el.parentElement;
  while (parent) {
    const pStyle = window.getComputedStyle(parent);
    if (pStyle.display === "none" || pStyle.visibility === "hidden") return false;
    parent = parent.parentElement;
  }

  return true;
}

/**
 * Extract visible text content from a node, normalized.
 */
export function getTextContent(el: Element): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}
