export type MatchConfidence = "strong" | "moderate" | "stretch" | "weak";

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
