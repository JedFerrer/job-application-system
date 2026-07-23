import { describe, expect, it } from "vitest";
import { getDisplayMatchConfidence } from "@/lib/match-display";

describe("getDisplayMatchConfidence", () => {
  it("uses saved AI confidence for a saved AI score", () => {
    expect(
      getDisplayMatchConfidence({
        confidence: "weak",
        llm_score: 80,
        llm_confidence: 0.9,
      }),
    ).toBe("strong");
  });

  it("keeps vector confidence when no saved AI confidence is available", () => {
    expect(
      getDisplayMatchConfidence({
        confidence: "weak",
        llm_score: null,
        llm_confidence: null,
      }),
    ).toBe("weak");
  });
});
