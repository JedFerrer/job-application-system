import type { TailoredResume, TailoredWorkItem } from "./types";

export type ReviewLabel = "Reworded" | "Moved higher" | "Reworded + moved higher";

export interface ReviewItem {
  key: string;
  outputIndex: number;
  sourceIndex: number;
  originalText: string;
  suggestionText: string;
  changed: boolean;
  moved: boolean;
  label: ReviewLabel | null;
}

export interface ReviewState {
  source: TailoredResume;
  rejected: Set<string>;
}

export function mappingIsValid(role: TailoredWorkItem): boolean {
  const mapping = role.tailored_highlight_source_indices;
  if (mapping == null || mapping.length !== role.tailored_highlights.length) return false;
  if (mapping.length !== role.original_highlights.length) return false;
  const expected = new Set(role.original_highlights.map((_, index) => index));
  return mapping.every(
    (index) => Number.isInteger(index) && expected.has(index),
  ) && new Set(mapping).size === mapping.length;
}

export function reviewItems(data: TailoredResume): ReviewItem[][] | null {
  if (!data.work_history.every(mappingIsValid)) return null;
  return data.work_history.map((role, roleIndex) =>
    role.tailored_highlights.map((suggestionText, outputIndex) => {
      const sourceIndex = role.tailored_highlight_source_indices![outputIndex]!;
      const originalText = role.original_highlights[sourceIndex]!;
      const changed = originalText !== suggestionText;
      const moved = sourceIndex !== outputIndex;
      const movedHigher = outputIndex < sourceIndex;
      const label = changed
        ? movedHigher
          ? "Reworded + moved higher"
          : "Reworded"
        : movedHigher
          ? "Moved higher"
          : null;
      return {
        key: `${roleIndex}:${sourceIndex}`,
        outputIndex,
        sourceIndex,
        originalText,
        suggestionText,
        changed,
        moved,
        label,
      };
    }),
  );
}

export function createReviewState(data: TailoredResume): ReviewState {
  return { source: data, rejected: new Set<string>() };
}

export function reviewedResume(state: ReviewState): TailoredResume {
  const work_history = state.source.work_history.map((role, roleIndex) => ({
    ...role,
    tailored_highlights: role.tailored_highlights.map((suggestion, outputIndex) => {
      const sourceIndex = role.tailored_highlight_source_indices?.[outputIndex];
      const key = `${roleIndex}:${sourceIndex}`;
      return state.rejected.has(key) && sourceIndex != null
        ? role.original_highlights[sourceIndex]!
        : suggestion;
    }),
  }));
  return { ...state.source, work_history };
}

export function sourceOnlyResume(data: TailoredResume): TailoredResume {
  return {
    ...data,
    work_history: data.work_history.map((role) => {
      const sourceRole = { ...role };
      delete sourceRole.tailored_highlight_source_indices;
      return {
        ...sourceRole,
        tailored_highlights: [...role.original_highlights],
      };
    }),
  };
}

export function wordDiff(original: string, suggestion: string): Array<{
  text: string;
  added?: boolean;
  removed?: boolean;
}> {
  const left = original.split(/(\s+)/);
  const right = suggestion.split(/(\s+)/);
  const result: Array<{ text: string; added?: boolean; removed?: boolean }> = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (left[i] === right[j]) {
      if (left[i] != null) result.push({ text: left[i]! });
      i++;
      j++;
    } else if (j < right.length && right[j] != null && !left.slice(i + 1).includes(right[j]!)) {
      result.push({ text: right[j]!, added: true });
      j++;
    } else {
      result.push({ text: left[i]!, removed: true });
      i++;
    }
  }
  return result;
}
