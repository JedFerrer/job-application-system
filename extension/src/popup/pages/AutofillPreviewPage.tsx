import { useState } from "react";
import type { AutofillProfile, DetectedField } from "../../shared/types";
import { SAFE_CONFIDENCE_THRESHOLD, REVIEW_CONFIDENCE_THRESHOLD } from "../../shared/constants";

interface Props {
  profile: AutofillProfile;
  fields: DetectedField[];
  onFill: (safeOnly: boolean) => void;
}

export default function AutofillPreviewPage({ profile, fields, onFill }: Props) {
  const [showAll, setShowAll] = useState(false);

  // Compute field mappings client-side (deterministic layer)
  const mappings = computeFieldMappings(fields, profile);
  const safeFields = mappings.filter((m) => m.action === "fill");
  const reviewFields = mappings.filter((m) => m.action === "review");
  const skippedFields = mappings.filter((m) => m.action === "skip");

  const displayFields = showAll ? mappings : [...safeFields, ...reviewFields];

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>
          Autofill Preview
        </h3>
        <div style={{ display: "flex", gap: 8, fontSize: 12, marginBottom: 4 }}>
          <span style={{ color: "#16a34a" }}>✓ {safeFields.length} safe</span>
          <span style={{ color: "#d97706" }}>⚠ {reviewFields.length} review</span>
          <span style={{ color: "var(--muted)" }}>✕ {skippedFields.length} skipped</span>
        </div>
      </div>

      <div
        style={{
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 8,
          marginBottom: 12,
          maxHeight: 280,
          overflowY: "auto",
        }}
      >
        {displayFields.map((m) => (
          <div
            key={m.elementId}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "6px 8px",
              fontSize: 12,
              borderBottom: "1px solid var(--border)",
            }}
          >
            <span style={{ flex: 1 }}>
              <span style={{ fontWeight: 500 }}>{m.label || m.elementId}</span>
              {m.value && (
                <span style={{ color: "var(--muted)", marginLeft: 8 }}>
                  → {m.value}
                </span>
              )}
            </span>
            <span>
              {m.action === "fill" && (
                <span style={{ color: "#16a34a" }}>✓</span>
              )}
              {m.action === "review" && (
                <span style={{ color: "#d97706" }}>⚠</span>
              )}
              {m.action === "skip" && (
                <span style={{ color: "var(--muted)" }}>✕</span>
              )}
            </span>
          </div>
        ))}
        {!showAll && skippedFields.length > 0 && (
          <button
            onClick={() => setShowAll(true)}
            style={{
              width: "100%",
              padding: "6px 0",
              background: "none",
              border: "none",
              color: "var(--muted)",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            Show {skippedFields.length} skipped fields
          </button>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={() => onFill(true)}
          style={{
            flex: 1,
            padding: "10px 0",
            background: "var(--accent)",
            color: "var(--accent-fg)",
            border: "none",
            borderRadius: 6,
            fontSize: 14,
            fontWeight: 500,
            cursor: "pointer",
          }}
        >
          Fill Safe Fields
        </button>
        {reviewFields.length > 0 && (
          <button
            onClick={() => onFill(false)}
            style={{
              flex: 1,
              padding: "10px 0",
              background: "none",
              border: "1px solid var(--border)",
              borderRadius: 6,
              fontSize: 14,
              cursor: "pointer",
              color: "var(--fg)",
            }}
          >
            Review All
          </button>
        )}
      </div>
    </div>
  );
}

// ── Deterministic Field Mapping (Client-Side Layer 1+2) ────────────────────

interface ComputedMapping {
  elementId: string;
  label: string | undefined;
  value: string | undefined;
  action: "fill" | "review" | "skip";
  confidence: number;
}

function computeFieldMappings(
  fields: DetectedField[],
  profile: AutofillProfile,
): ComputedMapping[] {
  return fields.map((field) => {
    // Skip invisible fields
    if (!field.visible) {
      return { elementId: field.elementId, label: field.label, value: undefined, action: "skip", confidence: 0 };
    }

    // Never fill password or file inputs
    if (field.inputType === "password" || field.inputType === "file") {
      return { elementId: field.elementId, label: field.label, value: undefined, action: "skip", confidence: 0 };
    }

    // Check sensitive labels
    const labelLower = (field.label ?? "").toLowerCase();
    const placeholderLower = (field.placeholder ?? "").toLowerCase();
    const ariaLower = (field.ariaLabel ?? "").toLowerCase();
    const combined = `${labelLower} ${placeholderLower} ${ariaLower}`;

    if (isSensitiveLabel(combined)) {
      return { elementId: field.elementId, label: field.label, value: undefined, action: "skip", confidence: 0 };
    }

    // Layer 1: browser autocomplete
    const autofill = field.autocomplete?.toLowerCase() ?? "";
    const autoResult = mapAutocomplete(autofill, profile);
    if (autoResult) return autoResult;

    // Layer 2: deterministic label matching
    const labelResult = mapLabel(combined, profile);
    if (labelResult) return labelResult;

    // No match — skip
    return { elementId: field.elementId, label: field.label, value: undefined, action: "skip", confidence: 0 };
  });
}

function isSensitiveLabel(text: string): boolean {
  const sensitivePatterns = [
    /race/i, /ethnicity/i, /gender/i, /sex(?!ual)/i, /disability/i,
    /veteran/i, /health/i, /criminal/i, /religion/i, /religious/i, /political/i,
    /eeo/i, /equal opportunity/i, /background check/i, /drug test/i,
    /citizenship/i, /national origin/i, /sexual orientation/i,
    /gender identity/i, /marital status/i, /military/i,
  ];
  return sensitivePatterns.some((p) => p.test(text));
}

function mapAutocomplete(
  autofill: string,
  profile: AutofillProfile,
): ComputedMapping | null {
  const map: Record<string, { path: string; value: string | undefined; tier: "fill" | "review" }> = {
    "given-name": { path: "personal.first_name", value: profile.personal.first_name ?? undefined, tier: "fill" },
    "family-name": { path: "personal.last_name", value: profile.personal.last_name ?? undefined, tier: "fill" },
    email: { path: "personal.email", value: profile.personal.email ?? undefined, tier: "fill" },
    tel: { path: "personal.phone", value: profile.personal.phone ?? undefined, tier: "fill" },
    "address-level2": { path: "personal.city", value: profile.personal.city ?? undefined, tier: "fill" },
    country: { path: "personal.country", value: profile.personal.country ?? undefined, tier: "fill" },
    organization: { path: "work.current_company", value: profile.work.current_company ?? undefined, tier: "review" },
    "organization-title": { path: "work.current_title", value: profile.work.current_title ?? undefined, tier: "fill" },
    url: { path: "links.website", value: profile.links.website ?? profile.links.portfolio ?? undefined, tier: "fill" },
  };

  const match = map[autofill];
  if (!match || !match.value) return null;

  return {
    elementId: "",
    label: autofill,
    value: match.value,
    action: match.tier,
    confidence: 0.98,
  };
}

function mapLabel(
  text: string,
  profile: AutofillProfile,
): ComputedMapping | null {
  const rules: Array<{ patterns: RegExp[]; value: string | undefined; tier: "fill" | "review" }> = [
    { patterns: [/\bfirst\s*name\b/i], value: profile.personal.first_name ?? undefined, tier: "fill" },
    { patterns: [/\blast\s*name\b/i, /\bsurname\b/i], value: profile.personal.last_name ?? undefined, tier: "fill" },
    { patterns: [/\bemail\b/i, /\be-mail\b/i], value: profile.personal.email ?? undefined, tier: "fill" },
    { patterns: [/\bphone\b/i, /\btelephone\b/i, /\bmobile\b/i], value: profile.personal.phone ?? undefined, tier: "fill" },
    { patterns: [/\bcity\b/i], value: profile.personal.city ?? undefined, tier: "fill" },
    { patterns: [/\bcountry\b/i], value: profile.personal.country ?? undefined, tier: "fill" },
    { patterns: [/\blinkedin\b/i], value: profile.links.linkedin ?? undefined, tier: "fill" },
    { patterns: [/\bgithub\b/i], value: profile.links.github ?? undefined, tier: "fill" },
    { patterns: [/\bportfolio\b/i, /\bwebsite\b/i], value: profile.links.portfolio ?? profile.links.website ?? undefined, tier: "fill" },
    { patterns: [/\bcurrent\s*title\b/i, /\bjob\s*title\b/i], value: profile.work.current_title ?? undefined, tier: "fill" },
    { patterns: [/\byears\s*of\s*experience\b/i, /\bexperience\b/i], value: profile.work.years_experience?.toString(), tier: "fill" },
    { patterns: [/\bexpected\s*salary\b/i, /\bdesired\s*salary\b/i, /\bsalary\s*expectation/i], value: profile.work.expected_salary ?? undefined, tier: "review" },
    { patterns: [/\bcurrent\s*company\b/i, /\bemployer\b/i], value: profile.work.current_company ?? undefined, tier: "review" },
    { patterns: [/\bsponsorship\b/i], value: profile.authorization.requires_sponsorship != null ? (profile.authorization.requires_sponsorship ? "Yes" : "No") : undefined, tier: "review" },
    { patterns: [/\bauthori[sz]ed\s*to\s*work\b/i, /\blegally\s*authori[sz]ed\b/i], value: profile.authorization.authorized_to_work != null ? (profile.authorization.authorized_to_work ? "Yes" : "No") : undefined, tier: "review" },
  ];

  for (const rule of rules) {
    if (rule.patterns.some((p) => p.test(text)) && rule.value) {
      return {
        elementId: "",
        label: text.substring(0, 40),
        value: rule.value,
        action: rule.tier,
        confidence: rule.tier === "fill" ? 0.96 : 0.80,
      };
    }
  }

  return null;
}
