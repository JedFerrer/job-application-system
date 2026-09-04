import type { TailoredEvidenceReference, TailoredResume, TailoredWorkItem } from "./types";

export type ReviewLabel = "Reworded" | "Moved higher" | "Reworded + moved higher" | "Comparison unavailable; showing source bullets safely.";

export interface ReviewItem {
  key: string;
  outputIndex: number;
  sourceIndex: number;
  originalText: string;
  suggestionText: string;
  changed: boolean;
  moved: boolean;
  label: ReviewLabel | null;
  included?: boolean;
  recommendedIncluded?: boolean;
  evidence?: TailoredEvidenceReference[];
}

export interface ReviewState {
  source: TailoredResume;
  rejected: Set<string>;
  finalIncluded: Set<string>;
  summaryRejected: boolean;
  skillsSourceOrder: boolean;
}

function decisionKey(roleIndex: number, sourceIndex: number): string {
  return `${roleIndex}:${sourceIndex}`;
}

export function mappingIsValid(role: TailoredWorkItem): boolean {
  const mapping = role.tailored_highlight_source_indices;
  if (mapping == null || mapping.length !== role.tailored_highlights.length) return false;
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
        evidence: undefined,
      };
    }),
  );
}

export function createReviewState(data: TailoredResume): ReviewState {
  const finalIncluded = new Set<string>();
  const rejected = new Set<string>();
  for (const [roleIndex, role] of data.work_history.entries()) {
    if (!role.selection_decisions?.length) {
      for (const sourceIndex of role.tailored_highlight_source_indices ?? []) {
        finalIncluded.add(decisionKey(roleIndex, sourceIndex));
      }
    }
    for (const decision of role.selection_decisions ?? []) {
      const key = decisionKey(roleIndex, decision.source_index);
      if (decision.final_included) finalIncluded.add(key);
      if (
        decision.suggested_text !== decision.original_text
        && decision.final_text === decision.original_text
      ) {
        rejected.add(key);
      }
    }
  }
  return {
    source: data,
    rejected,
    finalIncluded,
    summaryRejected: false,
    skillsSourceOrder: false,
  };
}

function sourceSummary(data: TailoredResume): string | null {
  const items = data.canonical_sections?.find((section) => section.section === "summary")?.items;
  if (!items?.length) return null;
  return items.reduce((joined, item) => {
    const next = item.text.trim();
    if (!next) return joined;
    if (!joined) return next;
    return joined.endsWith("-") ? `${joined}${next}` : `${joined} ${next}`;
  }, "") || null;
}

export function summaryReview(data: TailoredResume): { original: string; suggestion: string; changed: boolean } | null {
  const original = sourceSummary(data);
  if (!original || !data.professional_summary) return null;
  return { original, suggestion: data.professional_summary, changed: original !== data.professional_summary };
}

export function sourceSkillOrder(data: TailoredResume): { texts: string[]; ids: string[] } | null {
  const skills = data.canonical_sections?.find((section) => section.section === "skills")?.items;
  if (!skills?.length) return null;
  const ordered = skills.slice().sort((a, b) => a.canonical_order - b.canonical_order);
  return { texts: ordered.map((item) => item.text), ids: ordered.map((_, index) => `skill:${index}`) };
}

export function decisionItems(data: TailoredResume): ReviewItem[][] {
  return data.work_history.map((role, roleIndex) => {
    if (!role.selection_decisions?.length) {
      const mapped = reviewItems(data)?.[roleIndex];
      if (mapped) return mapped.map((item) => ({ ...item, included: true, recommendedIncluded: true }));
      return role.original_highlights.map((text, sourceIndex) => ({
        key: `${roleIndex}:${sourceIndex}`,
        outputIndex: sourceIndex,
        sourceIndex,
        originalText: text,
        suggestionText: text,
        changed: false,
        moved: false,
        label: "Comparison unavailable; showing source bullets safely.",
        included: true,
        recommendedIncluded: true,
        evidence: undefined,
      }));
    }
    const outputIndexBySource = new Map(
      (role.tailored_highlight_source_indices ?? []).map((sourceIndex, outputIndex) => [sourceIndex, outputIndex]),
    );
    return (role.selection_decisions ?? []).map((decision) => {
      const outputIndex = outputIndexBySource.get(decision.source_index) ?? -1;
      const changed = decision.original_text !== decision.suggested_text;
      const moved = outputIndex >= 0 && decision.source_index !== outputIndex;
      const movedHigher = outputIndex >= 0 && outputIndex < decision.source_index;
      return {
        key: decisionKey(roleIndex, decision.source_index),
        outputIndex,
        sourceIndex: decision.source_index,
        originalText: decision.original_text,
        suggestionText: decision.suggested_text,
        changed,
        moved,
        label: changed ? (movedHigher ? "Reworded + moved higher" : "Reworded") : (movedHigher ? "Moved higher" : null),
        included: decision.final_included,
        recommendedIncluded: decision.recommended_included,
        evidence: decision.evidence,
      };
    });
  });
}

export function reviewedResume(state: ReviewState): TailoredResume {
  const work_history = state.source.work_history.map((role, roleIndex) => ({
    ...role,
    tailored_highlights: role.selection_decisions?.length
      ? []
      : role.tailored_highlights.map((suggestion, outputIndex) => {
      const sourceIndex = role.tailored_highlight_source_indices?.[outputIndex];
      const key = `${roleIndex}:${sourceIndex}`;
      return state.rejected.has(key) && sourceIndex != null
        ? role.original_highlights[sourceIndex]!
        : suggestion;
    }),
    tailored_highlight_source_indices: role.selection_decisions?.length
      ? []
      : role.tailored_highlight_source_indices,
    selection_decisions: role.selection_decisions?.map((decision) => ({
      ...decision,
      final_included: state.finalIncluded.has(decisionKey(roleIndex, decision.source_index)),
      final_text: state.rejected.has(decisionKey(roleIndex, decision.source_index))
        ? decision.original_text
        : decision.suggested_text,
    })),
  }));
  for (const [roleIndex, role] of state.source.work_history.entries()) {
    if (!role.selection_decisions?.length) continue;
    const decisions = work_history[roleIndex]!.selection_decisions!;
    const included = decisions
      .filter((decision) => decision.final_included)
      .sort((a, b) => {
        if (a.recommended_included !== b.recommended_included) return a.recommended_included ? -1 : 1;
        return a.recommended_included
          ? (a.recommendation_rank ?? Number.MAX_SAFE_INTEGER) - (b.recommendation_rank ?? Number.MAX_SAFE_INTEGER)
          : a.canonical_order - b.canonical_order;
      });
    work_history[roleIndex]!.tailored_highlights = included.map((decision) => decision.final_text);
    work_history[roleIndex]!.tailored_highlight_source_indices = included.map((decision) => decision.source_index);
    const outputRankBySource = new Map(
      included.map((decision, outputRank) => [decision.source_index, outputRank]),
    );
    decisions.forEach((decision) => {
      decision.output_rank = outputRankBySource.get(decision.source_index) ?? null;
    });
  }
  const sourceSkills = state.skillsSourceOrder ? sourceSkillOrder(state.source) : null;
  return {
    ...state.source,
    professional_summary: state.summaryRejected ? sourceSummary(state.source) : state.source.professional_summary,
    summary_evidence_ids: state.summaryRejected ? [] : state.source.summary_evidence_ids,
    summary_evidence: state.summaryRejected ? [] : state.source.summary_evidence,
    skills: sourceSkills?.texts ?? state.source.skills,
    skill_order_source_ids: sourceSkills?.ids ?? state.source.skill_order_source_ids,
    work_history,
  };
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
