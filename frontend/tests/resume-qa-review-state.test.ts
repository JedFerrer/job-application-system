import { describe, expect, it } from "vitest";
import { createReviewState, reviewedResume } from "@/lib/tailoring-review";
import { qaWrapResume } from "./fixtures/tailored-resume-qa";

describe("Phase 4 QA review-state contracts", () => {
  it("accepts the suggested bullet by default", () => {
    const source = qaWrapResume();
    const reviewed = reviewedResume(createReviewState(source));
    expect(reviewed.work_history[0]!.tailored_highlights[0]).toBe(
      source.work_history[0]!.selection_decisions![0]!.suggested_text,
    );
    expect(reviewed.work_history[0]!.tailored_highlights[0]).not.toBe(
      source.work_history[0]!.selection_decisions![0]!.original_text,
    );
  });

  it("restores original wording without resurrecting the suggestion", () => {
    const source = qaWrapResume();
    const state = createReviewState(source);
    state.rejected.add("0:0");
    const reviewed = reviewedResume(state);
    const original = source.work_history[0]!.selection_decisions![0]!.original_text;
    const suggested = source.work_history[0]!.selection_decisions![0]!.suggested_text;
    expect(reviewed.work_history[0]!.tailored_highlights[0]).toBe(original);
    expect(reviewed.work_history[0]!.tailored_highlights.join("\n")).not.toContain(suggested);
    expect(reviewed.work_history[0]!.selection_decisions![0]!.final_text).toBe(original);
  });

  it("omits a supported bullet and reorders the remaining recommended-first output", () => {
    const source = qaWrapResume();
    const state = createReviewState(source);
    state.finalIncluded.delete("0:1");
    const reviewed = reviewedResume(state);
    expect(reviewed.work_history[0]!.tailored_highlights).toEqual([
      source.work_history[0]!.selection_decisions![0]!.suggested_text,
      source.work_history[0]!.original_highlights[2],
    ]);
    expect(reviewed.work_history[0]!.tailored_highlight_source_indices).toEqual([0, 2]);
    expect(reviewed.work_history[0]!.selection_decisions![1]).toEqual(
      expect.objectContaining({ final_included: false, output_rank: null }),
    );
    expect(reviewed.work_history[1]!.tailored_highlights).toEqual(
      source.work_history[1]!.tailored_highlights,
    );
  });

  it("rehydrates Keep Original and omission when a saved snapshot is reopened", () => {
    const source = qaWrapResume();
    const state = createReviewState(source);
    state.rejected.add("0:0");
    state.finalIncluded.delete("0:1");
    const saved = reviewedResume(state);
    const reopened = reviewedResume(createReviewState(saved));
    const original = source.work_history[0]!.selection_decisions![0]!.original_text;
    const suggested = source.work_history[0]!.selection_decisions![0]!.suggested_text;
    expect(createReviewState(saved).rejected.has("0:0")).toBe(true);
    expect(createReviewState(saved).finalIncluded.has("0:1")).toBe(false);
    expect(reopened.work_history[0]!.tailored_highlights).toEqual(saved.work_history[0]!.tailored_highlights);
    expect(reopened.work_history[0]!.tailored_highlights[0]).toBe(original);
    expect(reopened.work_history[0]!.tailored_highlights.join("\n")).not.toContain(suggested);
    expect(reopened.work_history[0]!.selection_decisions![0]!.final_text).toBe(original);
    expect(reopened.work_history[0]!.selection_decisions![1]!.final_included).toBe(false);
    expect(reopened.work_history[0]!.tailored_highlight_source_indices).toEqual([0, 2]);
  });
});
