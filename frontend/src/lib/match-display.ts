import type { MatchScoreComponents } from "@/lib/types";

export type MatchConfidence = "strong" | "moderate" | "stretch" | "weak";

export const EVIDENCE_CONFIDENCE_TOOLTIP =
  "Indicates how complete the saved job/profile evidence is. It is not AI confidence.";

const COMPONENT_DEFINITIONS = [
  ["required_skill_coverage", "Required skills"],
  ["preferred_skill_coverage", "Preferred skills"],
  ["semantic_similarity", "Profile relevance"],
  ["role_alignment", "Role alignment"],
  ["seniority_experience", "Experience alignment"],
  ["user_preferences", "Preferences"],
] as const satisfies ReadonlyArray<[keyof MatchScoreComponents, string]>;

export type DeterministicComponentDisplay = {
  key: keyof MatchScoreComponents;
  label: string;
  value: string;
};

/** Format a current deterministic score without turning missing data into zero. */
export function formatDeterministicFitScore(
  score: number | null | undefined,
): string | null {
  if (typeof score !== "number" || !Number.isFinite(score) || score < 0 || score > 100) {
    return null;
  }
  return `${Math.round(score)}%`;
}

/** Return sanitized, ordered component values suitable for user-facing detail UI. */
export function getDeterministicComponentDisplay(
  components: MatchScoreComponents | null | undefined,
): DeterministicComponentDisplay[] {
  if (!components) return [];
  return COMPONENT_DEFINITIONS.flatMap(([key, label]) => {
    const component = components[key];
    if (!component) return [];
    if (component.status === "unknown") return [{ key, label, value: "Not available" }];
    if (component.status !== "known") return [];
    const value = formatDeterministicFitScore(component.score);
    return value ? [{ key, label, value }] : [];
  });
}

export function getEvidenceConfidenceLabel(
  confidence: "high" | "medium" | "low" | null | undefined,
): string | null {
  if (confidence == null || !["high", "medium", "low"].includes(confidence)) return null;
  return `${confidence[0]!.toUpperCase()}${confidence.slice(1)} evidence`;
}

/** Return the confidence band for the quick vector score used to rank the feed. */
export function getQuickMatchConfidence(score: number): MatchConfidence {
  if (score >= 80) return "strong";
  if (score >= 65) return "moderate";
  if (score >= 50) return "stretch";
  return "weak";
}

/** Translate the detailed AI assessment confidence into user-facing fit language. */
export function getAiFitLabel(confidence: number | null | undefined): string | null {
  if (confidence == null) return null;
  if (confidence >= 0.8) return "Strong fit";
  if (confidence >= 0.65) return "Good fit";
  if (confidence >= 0.5) return "Stretch fit";
  return "Needs review";
}
