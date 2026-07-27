import { describe, expect, it } from "vitest";
import {
  EVIDENCE_CONFIDENCE_TOOLTIP,
  formatDeterministicFitScore,
  getAiFitLabel,
  getDeterministicComponentDisplay,
  getEvidenceConfidenceLabel,
  getQuickMatchConfidence,
} from "@/lib/match-display";

describe("getQuickMatchConfidence", () => {
  it("uses the quick score bands that power feed ranking", () => {
    expect(getQuickMatchConfidence(80)).toBe("strong");
    expect(getQuickMatchConfidence(65)).toBe("moderate");
    expect(getQuickMatchConfidence(50)).toBe("stretch");
    expect(getQuickMatchConfidence(49.99)).toBe("weak");
  });
});

describe("getAiFitLabel", () => {
  it("turns the detailed assessment confidence into qualitative fit language", () => {
    expect(getAiFitLabel(0.9)).toBe("Strong fit");
    expect(getAiFitLabel(0.7)).toBe("Good fit");
    expect(getAiFitLabel(0.5)).toBe("Stretch fit");
    expect(getAiFitLabel(0.2)).toBe("Needs review");
    expect(getAiFitLabel(null)).toBeNull();
  });
});

describe("deterministic fit display", () => {
  it("formats valid, zero, and missing scores safely", () => {
    expect(formatDeterministicFitScore(81.6)).toBe("82%");
    expect(formatDeterministicFitScore(0)).toBe("0%");
    expect(formatDeterministicFitScore(null)).toBeNull();
    expect(formatDeterministicFitScore(undefined)).toBeNull();
    expect(formatDeterministicFitScore(Number.NaN)).toBeNull();
    expect(formatDeterministicFitScore(Infinity)).toBeNull();
    expect(formatDeterministicFitScore(-Infinity)).toBeNull();
    expect(formatDeterministicFitScore("82" as unknown as number)).toBeNull();
    expect(formatDeterministicFitScore(true as unknown as number)).toBeNull();
    expect(formatDeterministicFitScore({} as unknown as number)).toBeNull();
    expect(formatDeterministicFitScore([] as unknown as number)).toBeNull();
    expect(formatDeterministicFitScore(101)).toBeNull();
  });

  it("orders readable component labels and handles partial, zero, and unknown values", () => {
    expect(getDeterministicComponentDisplay({
      user_preferences: { score: 0, weight: 5, status: "known", reason: "x" },
      semantic_similarity: { score: 75.4, weight: 15, status: "known", reason: "x" },
      seniority_experience: { score: 50, weight: 15, status: "unknown", reason: "x" },
    })).toEqual([
      { key: "semantic_similarity", label: "Profile relevance", value: "75%" },
      { key: "seniority_experience", label: "Experience alignment", value: "Not available" },
      { key: "user_preferences", label: "Preferences", value: "0%" },
    ]);
    expect(getDeterministicComponentDisplay(null)).toEqual([]);
    expect(getDeterministicComponentDisplay(undefined)).toEqual([]);
    expect(getDeterministicComponentDisplay({
      required_skill_coverage: {
        score: Infinity as unknown as number,
        weight: 35,
        status: "known",
        reason: "x",
      },
    })).toEqual([]);
  });

  it("maps evidence confidence without conflating it with AI confidence", () => {
    expect(getEvidenceConfidenceLabel("high")).toBe("High evidence");
    expect(getEvidenceConfidenceLabel("medium")).toBe("Medium evidence");
    expect(getEvidenceConfidenceLabel("low")).toBe("Low evidence");
    expect(getEvidenceConfidenceLabel(null)).toBeNull();
    expect(getEvidenceConfidenceLabel(undefined)).toBeNull();
    expect(EVIDENCE_CONFIDENCE_TOOLTIP).toContain("not AI confidence");
  });
});
