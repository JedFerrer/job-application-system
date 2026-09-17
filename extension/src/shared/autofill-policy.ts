import type { AutofillProfile, DetectedField, FieldMapping } from "./types";

type MappingAction = "fill" | "review";

interface FieldDefinition {
  profilePath: string;
  action: MappingAction;
  labels: readonly string[];
  autocomplete?: readonly string[];
  value: (profile: AutofillProfile) => string | undefined;
}

export const EXECUTABLE_SAFE_PROFILE_PATHS: ReadonlySet<string> = new Set([
  "personal.first_name",
  "personal.last_name",
  "personal.email",
  "personal.phone",
  "personal.city",
  "personal.country",
  "links.linkedin",
  "links.github",
  "links.portfolio",
  "links.website",
  "work.current_title",
]);

export const SENSITIVE_LABEL_PATTERNS: readonly RegExp[] = [
  /race/i, /ethnicity/i, /gender/i, /sex(?!ual)/i, /disability/i,
  /veteran/i, /health/i, /criminal/i, /religion/i, /religious/i, /political/i,
  /eeo/i, /equal opportunity/i, /background check/i, /drug test/i,
  /citizenship/i, /authorized to work/i, /national origin/i,
  /sexual orientation/i, /gender identity/i, /marital status/i, /military/i,
];

const FIELD_DEFINITIONS: readonly FieldDefinition[] = [
  { profilePath: "personal.first_name", action: "fill", labels: ["first name", "preferred first name"], autocomplete: ["given-name"], value: (p) => p.personal.first_name ?? undefined },
  { profilePath: "personal.last_name", action: "fill", labels: ["last name", "surname"], autocomplete: ["family-name"], value: (p) => p.personal.last_name ?? undefined },
  { profilePath: "personal.email", action: "fill", labels: ["email", "email address"], autocomplete: ["email"], value: (p) => p.personal.email ?? undefined },
  { profilePath: "personal.phone", action: "fill", labels: ["phone", "phone number", "telephone", "mobile"], autocomplete: ["tel"], value: (p) => p.personal.phone ?? undefined },
  { profilePath: "personal.city", action: "fill", labels: ["city"], autocomplete: ["address-level2"], value: (p) => p.personal.city ?? undefined },
  { profilePath: "personal.country", action: "fill", labels: ["country"], autocomplete: ["country"], value: (p) => p.personal.country ?? undefined },
  { profilePath: "links.linkedin", action: "fill", labels: ["linkedin", "linkedin profile", "linkedin url"], value: (p) => p.links.linkedin ?? undefined },
  { profilePath: "links.github", action: "fill", labels: ["github", "github url"], value: (p) => p.links.github ?? undefined },
  { profilePath: "links.portfolio", action: "fill", labels: ["portfolio", "portfolio url"], value: (p) => p.links.portfolio ?? p.links.website ?? undefined },
  { profilePath: "links.website", action: "fill", labels: ["website", "website url"], autocomplete: ["url"], value: (p) => p.links.website ?? p.links.portfolio ?? undefined },
  { profilePath: "work.current_title", action: "fill", labels: ["current title", "current job title"], autocomplete: ["organization-title"], value: (p) => p.work.current_title ?? undefined },
  { profilePath: "work.expected_salary", action: "review", labels: ["expected salary", "desired salary", "salary expectation"], value: (p) => p.work.expected_salary ?? undefined },
  { profilePath: "work.current_company", action: "review", labels: ["current company", "employer"], autocomplete: ["organization"], value: (p) => p.work.current_company ?? undefined },
  { profilePath: "authorization.requires_sponsorship", action: "review", labels: ["sponsorship", "requires sponsorship"], value: (p) => p.authorization.requires_sponsorship == null ? undefined : p.authorization.requires_sponsorship ? "Yes" : "No" },
];

const LABEL_SAFE_INPUT_TYPES = new Set(["text", "email", "tel", "search", "url"]);
const BLOCKED_CONTROL_TYPES = new Set(["password", "file", "radio", "checkbox"]);

export function normalizeAutofillLabel(label: string | undefined): string {
  return (label ?? "").toLowerCase().trim().replace(/\s+/g, " ").replace(/[\s*:]+$/, "").trim();
}

export function classifyAutofillField(field: DetectedField, profile: AutofillProfile): FieldMapping {
  const base = { elementId: field.elementId, selectorPath: field.selectorPath };
  if (!field.visible) return { ...base, profilePath: null, confidence: 0, action: "skip", reasonSkipped: "Field is not visible" };
  if (isBlockedControl(field)) return { ...base, profilePath: null, confidence: 0, action: "skip", reasonSkipped: "Protected field" };
  if (isSensitiveField(field)) return { ...base, profilePath: null, confidence: 0, action: "skip", reasonSkipped: "Sensitive field" };

  const autocomplete = field.autocomplete?.toLowerCase().trim();
  const autocompleteMatch = autocomplete ? FIELD_DEFINITIONS.find((definition) => definition.autocomplete?.includes(autocomplete) && isAutocompleteCompatible(field)) : undefined;
  const label = normalizeAutofillLabel(field.label);
  const labelMatch = !autocompleteMatch && isLabelCompatible(field)
    ? FIELD_DEFINITIONS.find((definition) => definition.labels.includes(label))
    : undefined;
  const match = autocompleteMatch ?? labelMatch;
  const value = match?.value(profile);

  if (!match || !value) return { ...base, profilePath: null, confidence: 0, action: "skip", reasonSkipped: "No safe mapping" };
  return { ...base, profilePath: match.profilePath, value, action: match.action, confidence: match.action === "fill" ? 0.98 : 0.8 };
}

function isBlockedControl(field: DetectedField): boolean {
  return BLOCKED_CONTROL_TYPES.has((field.inputType ?? "").toLowerCase());
}

function isAutocompleteCompatible(field: DetectedField): boolean {
  return !isBlockedControl(field);
}

function isLabelCompatible(field: DetectedField): boolean {
  return field.tagName === "input" && LABEL_SAFE_INPUT_TYPES.has((field.inputType ?? "text").toLowerCase());
}

function isSensitiveField(field: DetectedField): boolean {
  const text = `${field.label ?? ""} ${field.placeholder ?? ""} ${field.ariaLabel ?? ""}`.toLowerCase();
  return SENSITIVE_LABEL_PATTERNS.some((pattern) => pattern.test(text));
}
