import type { TailoredBulletDecision, TailoredResume, TailoredWorkItem } from "./types";

export interface RenderedResumeBullet {
  sourceId: string | null;
  sourceIndex: number | null;
  text: string;
  outputRank: number;
}

export interface RenderedResumeRole {
  company: string | null;
  title: string | null;
  start: string | null;
  end: string | null;
  header: string | null;
  narrative: string | null;
  responsibilityHeading: string | null;
  bullets: RenderedResumeBullet[];
}

function joinNarrative(values: string[] | undefined): string | null {
  if (!values?.length) return null;
  const joined = values.reduce((acc, value) => {
    const next = value.trim();
    if (!next) return acc;
    if (!acc) return next;
    return acc.endsWith("-") ? `${acc}${next}` : `${acc} ${next}`;
  }, "");
  return joined || null;
}

function hasValidOutputRanks(decisions: TailoredBulletDecision[]): boolean {
  const ranks = decisions.map((decision) => decision.output_rank);
  return (
    ranks.every((rank) => rank != null && Number.isInteger(rank) && rank >= 0)
    && new Set(ranks).size === ranks.length
  );
}

function bulletsFromReviewedOrder(
  role: TailoredWorkItem,
  included: TailoredBulletDecision[],
): TailoredBulletDecision[] {
  const bySourceIndex = new Map(included.map((decision) => [decision.source_index, decision]));
  const fromMapping = (role.tailored_highlight_source_indices ?? [])
    .map((sourceIndex) => bySourceIndex.get(sourceIndex))
    .filter((decision): decision is TailoredBulletDecision => Boolean(decision));
  if (fromMapping.length === included.length) return fromMapping;
  return included;
}

function bulletsFromDecisions(role: TailoredWorkItem): RenderedResumeBullet[] | null {
  const decisions = role.selection_decisions;
  if (!decisions?.length) return null;
  const included = decisions.filter((decision) => decision.final_included);
  const ordered = hasValidOutputRanks(included)
    ? [...included].sort((a, b) => (a.output_rank ?? 0) - (b.output_rank ?? 0))
    : bulletsFromReviewedOrder(role, included);
  return ordered.map((decision, outputRank) => ({
    sourceId: decision.source_id,
    sourceIndex: decision.source_index,
    text: decision.final_text,
    outputRank: decision.output_rank ?? outputRank,
  }));
}

function bulletsFromHighlights(role: TailoredWorkItem): RenderedResumeBullet[] {
  return role.tailored_highlights.map((text, outputRank) => ({
    sourceId: null,
    sourceIndex: role.tailored_highlight_source_indices?.[outputRank] ?? null,
    text,
    outputRank,
  }));
}

/** Canonical reviewed work-history content shared by preview, copy, PDF, and DOCX. */
export function buildReviewedWorkHistory(data: TailoredResume): RenderedResumeRole[] {
  return data.work_history.map((role) => {
    const bullets = bulletsFromDecisions(role) ?? bulletsFromHighlights(role);
    const heading = role.responsibilities_heading?.trim() || null;
    return {
      company: role.company,
      title: role.title,
      start: role.start,
      end: role.end,
      header: role.source_header || null,
      narrative: joinNarrative(role.role_context),
      responsibilityHeading: heading && bullets.length ? heading : null,
      bullets,
    };
  });
}

/** Medium-agnostic work-history lines for cross-surface equality checks. */
export function semanticWorkHistoryLines(data: TailoredResume): string[] {
  return buildReviewedWorkHistory(data).flatMap((role) => {
    const lines: string[] = [];
    if (role.narrative) lines.push(role.narrative);
    if (role.responsibilityHeading) lines.push(role.responsibilityHeading);
    for (const bullet of role.bullets) lines.push(bullet.text);
    return lines;
  });
}
