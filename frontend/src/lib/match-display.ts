type MatchScoreSource = {
  confidence: string | null;
  llm_score: number | null;
  llm_confidence?: number | null;
};

/** Return the confidence band that belongs to the score displayed in the UI. */
export function getDisplayMatchConfidence(match: MatchScoreSource): string | null {
  if (match.llm_score == null || match.llm_confidence == null) return match.confidence;
  if (match.llm_confidence >= 0.8) return "strong";
  if (match.llm_confidence >= 0.65) return "moderate";
  if (match.llm_confidence >= 0.5) return "stretch";
  return "weak";
}
