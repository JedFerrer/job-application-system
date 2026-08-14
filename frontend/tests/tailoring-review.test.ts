import { describe, expect, it } from "vitest";
import { createReviewState, mappingIsValid, reviewItems, reviewedResume, sourceOnlyResume, wordDiff } from "@/lib/tailoring-review";
import type { TailoredResume } from "@/lib/types";

const resume = (tailored = ["Built Python APIs", "Built Python APIs"]): TailoredResume => ({
  job_id: 1,
  professional_summary: null,
  full_name: null,
  email: null,
  phone: null,
  headline: null,
  work_history: [{ company: "Acme", title: "Engineer", start: null, end: null, original_highlights: ["Built APIs", "Built APIs"], tailored_highlights: tailored, tailored_highlight_source_indices: [1, 0] }],
  education: [], certifications: [], matched_skills: [], other_skills: [], missing_keywords: [],
});

describe("tailoring review", () => {
  it("maps duplicate source text by index and classifies rewrites/reorders", () => {
    const items = reviewItems(resume())!;
    expect(items[0]!.map((item) => item.label)).toEqual(["Reworded + moved higher", "Reworded"]);
  });
  it("labels unchanged bullets only when they move higher", () => {
    const items = reviewItems(resume(["Built APIs", "Built APIs"]))!;
    expect(items[0]!.map((item) => item.label)).toEqual(["Moved higher", null]);
  });
  it("accepts the mixed fallback shape emitted by the backend", () => {
    const mixed = resume();
    mixed.work_history[0] = {
      ...mixed.work_history[0]!,
      original_highlights: ["A", "B", "C", "D", "E"],
      tailored_highlights: ["B rewritten", "C", "A", "D", "E"],
      tailored_highlight_source_indices: [1, 2, 0, 3, 4],
    };
    expect(mappingIsValid(mixed.work_history[0]!)).toBe(true);
    expect(reviewItems(mixed)?.[0]?.[0]?.label).toBe("Reworded + moved higher");
  });
  it("projects selected subsets without requiring every source bullet", () => {
    const subset = resume(["Built APIs"]);
    subset.work_history[0] = {
      ...subset.work_history[0]!,
      original_highlights: ["Built APIs", "Unrelated mobile UI"],
      tailored_highlights: ["Built APIs"],
      tailored_highlight_source_indices: [0],
      selection_decisions: [
        {
          source_id: "00000000-0000-0000-0000-000000000001",
          source_index: 0,
          canonical_order: 0,
          original_text: "Built APIs",
          suggested_text: "Built APIs",
          final_text: "Built APIs",
          recommended_included: true,
          final_included: true,
        },
        {
          source_id: "00000000-0000-0000-0000-000000000002",
          source_index: 1,
          canonical_order: 1,
          original_text: "Unrelated mobile UI",
          suggested_text: "Unrelated mobile UI",
          final_text: "Unrelated mobile UI",
          recommended_included: false,
          final_included: false,
        },
      ],
    };
    expect(mappingIsValid(subset.work_history[0]!)).toBe(true);
    expect(reviewItems(subset)?.[0]).toHaveLength(1);
    expect(reviewedResume(createReviewState(subset)).work_history[0]!.selection_decisions?.[1]?.final_included).toBe(false);
  });
  it("rejects invalid mappings without text matching", () => {
    const invalid = resume();
    invalid.work_history[0]!.tailored_highlight_source_indices = [0];
    expect(mappingIsValid(invalid.work_history[0]!)).toBe(false);
    expect(reviewItems(invalid)).toBeNull();
    expect(sourceOnlyResume(invalid).work_history[0]!.tailored_highlights).toEqual(["Built APIs", "Built APIs"]);
    expect(sourceOnlyResume(invalid).work_history[0]).not.toHaveProperty("tailored_highlight_source_indices");
  });
  it("constructs reviewed content and restores suggestions", () => {
    const state = createReviewState(resume());
    state.rejected.add("0:1");
    expect(reviewedResume(state).work_history[0]!.tailored_highlights).toEqual(["Built APIs", "Built Python APIs"]);
    state.rejected.clear();
    expect(reviewedResume(state).work_history[0]!.tailored_highlights).toEqual(["Built Python APIs", "Built Python APIs"]);
  });
  it("provides deterministic visual diff segments", () => {
    expect(wordDiff("Built APIs", "Built Python APIs").some((part) => part.added)).toBe(true);
  });
});
