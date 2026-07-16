import { describe, expect, it } from "vitest";
import {
  getReadinessActionNavigationTarget,
  getReadinessActionSuggestion,
} from "@/lib/readiness-actions";

describe("getReadinessActionSuggestion", () => {
  const baseReadiness = {
    decision: "apply" as const,
    reasons: [],
    caveats: [],
    decision_basis: "score" as const,
  };

  it("suggests skipping when blockers exist", () => {
    expect(
      getReadinessActionSuggestion({
        ...baseReadiness,
        deterministic_blockers: ["Blocked keyword"],
        deterministic_warnings: [],
      }),
    ).toEqual({ action: "skip", label: "Skip", explanation: "Based on your rules" });
  });

  it("suggests review when only warnings exist", () => {
    expect(
      getReadinessActionSuggestion({
        ...baseReadiness,
        deterministic_blockers: [],
        deterministic_warnings: ["Salary needs review"],
      }),
    ).toEqual({ action: "review", label: "Review", explanation: "Based on your rules" });
  });

  it("suggests applying for score-only readiness", () => {
    expect(
      getReadinessActionSuggestion({
        ...baseReadiness,
        deterministic_blockers: [],
        deterministic_warnings: [],
      }),
    ).toEqual({ action: "apply", label: "Apply", explanation: "Based on match score" });
  });

  it("returns no suggestion when readiness is missing", () => {
    expect(getReadinessActionSuggestion(undefined)).toBeNull();
  });

  it("maps suggestions to advisory navigation destinations", () => {
    expect(
      getReadinessActionNavigationTarget({
        action: "apply",
        label: "Apply",
        explanation: "Based on match score",
      }),
    ).toBe("apply");
    expect(
      getReadinessActionNavigationTarget({
        action: "review",
        label: "Review",
        explanation: "Based on your rules",
      }),
    ).toBe("readiness");
    expect(
      getReadinessActionNavigationTarget({
        action: "skip",
        label: "Skip",
        explanation: "Based on your rules",
      }),
    ).toBe("archive");
    expect(getReadinessActionNavigationTarget(null)).toBeNull();
  });
});
