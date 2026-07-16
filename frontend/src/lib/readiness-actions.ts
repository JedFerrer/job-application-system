import type { MatchReadiness } from "@/lib/types";

export type SuggestedReadinessAction = "skip" | "review" | "apply";

export interface ReadinessActionSuggestion {
  action: SuggestedReadinessAction;
  label: string;
  explanation: "Based on your rules" | "Based on match score";
}

export type ReadinessActionNavigationTarget =
  | "apply"
  | "save"
  | "archive"
  | "readiness";

/** Return an advisory suggestion without changing any job or application state. */
export function getReadinessActionSuggestion(
  readiness: MatchReadiness | null | undefined,
): ReadinessActionSuggestion | null {
  if (!readiness) return null;

  if ((readiness.deterministic_blockers ?? []).length > 0) {
    return { action: "skip", label: "Skip", explanation: "Based on your rules" };
  }

  if ((readiness.deterministic_warnings ?? []).length > 0) {
    return { action: "review", label: "Review", explanation: "Based on your rules" };
  }

  return { action: "apply", label: "Apply", explanation: "Based on match score" };
}

/** Map an advisory suggestion to an existing UI destination; never to a mutation. */
export function getReadinessActionNavigationTarget(
  suggestion: ReadinessActionSuggestion | null,
): ReadinessActionNavigationTarget | null {
  if (!suggestion) return null;

  switch (suggestion.action) {
    case "apply":
      return "apply";
    case "review":
      return "readiness";
    case "skip":
      return "archive";
  }
}
