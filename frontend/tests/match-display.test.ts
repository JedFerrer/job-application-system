import { describe, expect, it } from "vitest";
import { getAiFitLabel, getQuickMatchConfidence } from "@/lib/match-display";

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
