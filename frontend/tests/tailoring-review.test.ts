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
  it("preserves immutable unclassified canonical passthrough blocks during review", () => {
    const source = resume();
    source.canonical_sections = [{
      section: "unclassified",
      canonical_order: 4,
      items: [{ source_id: "00000000-0000-0000-0000-000000000001", text: "Literal passthrough", canonical_order: 4 }],
    }];

    const reviewed = reviewedResume(createReviewState(source));

    expect(reviewed.canonical_sections).toEqual(source.canonical_sections);
  });
  it("provides deterministic visual diff segments", () => {
    expect(wordDiff("Built APIs", "Built Python APIs").some((part) => part.added)).toBe(true);
  });

  it("projects final inclusion and deterministic ordering from decision state", () => {
    const source = resume(["Recommended", "Second"]);
    source.work_history[0] = {
      ...source.work_history[0]!,
      original_highlights: ["Recommended", "Second", "Omitted"],
      tailored_highlights: ["Recommended", "Second"],
      tailored_highlight_source_indices: [0, 1],
      selection_decisions: [
        { source_id: "00000000-0000-0000-0000-000000000001", source_index: 0, canonical_order: 0, original_text: "Recommended", suggested_text: "Recommended", final_text: "Recommended", recommended_included: true, final_included: true, recommendation_rank: 0, output_rank: 0 },
        { source_id: "00000000-0000-0000-0000-000000000002", source_index: 1, canonical_order: 1, original_text: "Second", suggested_text: "Second", final_text: "Second", recommended_included: true, final_included: true, recommendation_rank: 1, output_rank: 1 },
        { source_id: "00000000-0000-0000-0000-000000000003", source_index: 2, canonical_order: 2, original_text: "Omitted", suggested_text: "Omitted", final_text: "Omitted", recommended_included: false, final_included: false },
      ],
    };
    const state = createReviewState(source);
    state.finalIncluded.delete("0:0");
    state.finalIncluded.add("0:2");
    const reviewed = reviewedResume(state);
    expect(reviewed.work_history[0]!.tailored_highlights).toEqual(["Second", "Omitted"]);
    expect(reviewed.work_history[0]!.tailored_highlight_source_indices).toEqual([1, 2]);
    expect(reviewed.work_history[0]!.selection_decisions).toEqual(expect.arrayContaining([
      expect.objectContaining({ source_index: 0, final_included: false, output_rank: null }),
      expect.objectContaining({ source_index: 1, final_included: true, output_rank: 0 }),
      expect.objectContaining({ source_index: 2, final_included: true, output_rank: 1 }),
    ]));

    state.finalIncluded = new Set(["0:0", "0:1"]);
    const restored = reviewedResume(state);
    expect(restored.work_history[0]!.selection_decisions).toEqual(expect.arrayContaining([
      expect.objectContaining({ source_index: 0, final_included: true, output_rank: 0 }),
      expect.objectContaining({ source_index: 1, final_included: true, output_rank: 1 }),
      expect.objectContaining({ source_index: 2, final_included: false, output_rank: null }),
    ]));

    state.finalIncluded.delete("0:0");
    const omittedAgain = reviewedResume(state);
    expect(omittedAgain.work_history[0]!.selection_decisions?.find((item) => item.source_index === 0)).toEqual(
      expect.objectContaining({ final_included: false, output_rank: null }),
    );
  });

  it("restores recommendation inclusion without changing wording overrides", () => {
    const source = resume(["Suggested", "Other"]);
    source.work_history[0] = {
      ...source.work_history[0]!,
      original_highlights: ["Original", "Other"],
      tailored_highlights: ["Suggested"],
      tailored_highlight_source_indices: [0],
      selection_decisions: [
        { source_id: "00000000-0000-0000-0000-000000000001", source_index: 0, canonical_order: 0, original_text: "Original", suggested_text: "Suggested", final_text: "Original", recommended_included: true, final_included: true, recommendation_rank: 0, output_rank: 0 },
        { source_id: "00000000-0000-0000-0000-000000000002", source_index: 1, canonical_order: 1, original_text: "Other", suggested_text: "Other", final_text: "Other", recommended_included: false, final_included: false },
      ],
    };
    const state = createReviewState(source);
    state.finalIncluded.delete("0:0");
    state.finalIncluded.add("0:1");
    state.rejected.add("0:0");
    const reviewed = reviewedResume(state);
    expect(reviewed.work_history[0]!.selection_decisions?.[0]?.final_included).toBe(false);
    expect(reviewed.work_history[0]!.selection_decisions?.[0]?.final_text).toBe("Original");
  });
});
