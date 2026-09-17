import { useMemo, useState } from "react";
import type { AutofillProfile, DetectedField, FieldMapping, FillExecutionResult } from "../../shared/types";
import { classifyAutofillField } from "../../shared/autofill-policy";

interface Props {
  profile: AutofillProfile;
  fields: DetectedField[];
  execution?: FillExecutionResult;
  filling: boolean;
  error?: string;
  onFill: (mappings: FieldMapping[]) => void;
  onBack: () => void;
}

export default function AutofillPreviewPage({ profile, fields, execution, filling, error, onFill, onBack }: Props) {
  const [showAll, setShowAll] = useState(false);
  const mappings = useMemo(() => computeFieldMappings(fields, profile), [fields, profile]);
  const safeFields = mappings.filter((mapping) => mapping.action === "fill");
  const reviewFields = mappings.filter((mapping) => mapping.action === "review");
  const skippedFields = mappings.filter((mapping) => mapping.action === "skip");
  const displayFields = showAll ? mappings : [...safeFields, ...reviewFields];

  return <div>
    <div style={{ marginBottom: 12 }}>
      <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Autofill Preview</h3>
      <div style={{ display: "flex", gap: 8, fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: "#16a34a" }}>✓ {safeFields.length} safe</span>
        <span style={{ color: "#d97706" }}>⚠ {reviewFields.length} review</span>
        <span style={{ color: "var(--muted)" }}>✕ {skippedFields.length} skipped</span>
      </div>
    </div>
    <div style={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, marginBottom: 12, maxHeight: 280, overflowY: "auto" }}>
      {displayFields.map((mapping) => <div key={mapping.elementId} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 8px", fontSize: 12, borderBottom: "1px solid var(--border)" }}>
        <span style={{ flex: 1 }}><span style={{ fontWeight: 500 }}>{labelFor(mapping, fields)}</span>{mapping.value && <span style={{ color: "var(--muted)", marginLeft: 8 }}>→ {mapping.value}</span>}</span>
        <span>{mapping.action === "fill" ? <span style={{ color: "#16a34a" }}>✓</span> : mapping.action === "review" ? <span style={{ color: "#d97706" }}>⚠</span> : <span style={{ color: "var(--muted)" }}>✕</span>}</span>
      </div>)}
      {!showAll && skippedFields.length > 0 && <button onClick={() => setShowAll(true)} style={{ width: "100%", padding: "6px 0", background: "none", border: "none", color: "var(--muted)", fontSize: 12, cursor: "pointer" }}>Show {skippedFields.length} skipped fields</button>}
    </div>
    {execution && <div style={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, marginBottom: 12, fontSize: 12 }}>
      <strong>Actual execution result</strong><div style={{ display: "flex", gap: 8, marginTop: 6 }}><span>{execution.attempted} attempted</span><span>{execution.filled} filled</span><span>{execution.failed} failed</span><span>{execution.skipped} skipped</span></div>
    </div>}
    {error && <p style={{ color: "var(--destructive)", fontSize: 12, marginBottom: 12 }}>{error}</p>}
    <div style={{ display: "flex", gap: 8 }}>
      <button onClick={() => onFill(mappings)} disabled={filling || safeFields.length === 0} style={{ flex: 1, padding: "10px 0", background: "var(--accent)", color: "var(--accent-fg)", border: "none", borderRadius: 6, fontSize: 14, fontWeight: 500, cursor: filling || safeFields.length === 0 ? "not-allowed" : "pointer" }}>{filling ? "Filling…" : "Fill Safe Fields"}</button>
      <button onClick={onBack} disabled={filling} style={{ padding: "10px 12px", background: "none", border: "1px solid var(--border)", borderRadius: 6, fontSize: 14, cursor: "pointer", color: "var(--fg)" }}>Back</button>
    </div>
  </div>;
}

function labelFor(mapping: FieldMapping, fields: DetectedField[]): string {
  return fields.find((field) => field.elementId === mapping.elementId)?.label ?? mapping.elementId;
}

function computeFieldMappings(fields: DetectedField[], profile: AutofillProfile): FieldMapping[] {
  return fields.map((field) => classifyAutofillField(field, profile));
}
