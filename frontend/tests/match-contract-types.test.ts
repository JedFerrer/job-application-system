import { describe, expect, it } from "vitest";
import type {
  MatchExplanation,
  MatchItem,
  MatchSkillAnalysis,
  MatchSummary,
} from "@/lib/types";

describe("deterministic match API contracts", () => {
  it("accepts populated, partial, omitted, and null additive fields", () => {
    const components = {
      required_skill_coverage: {
        score: 0,
        weight: 35,
        status: "known" as const,
        reason: "matched_required_coverage",
      },
    };
    const populated = {
      job_id: 1,
      vector_score: 72,
      fit_score: 68,
      score_components: components,
      evidence_confidence: "high" as const,
      confidence: null,
      llm_score: null,
      matched_skills: [],
      missing_keywords: [],
      readiness: {
        decision: "save" as const,
        reasons: [],
        caveats: [],
        deterministic_blockers: [],
        deterministic_warnings: [],
        decision_basis: "score" as const,
      },
    } satisfies MatchSummary;

    const legacy = {
      ...populated,
      fit_score: undefined,
      score_components: null,
      evidence_confidence: null,
    } satisfies MatchSummary;

    const listItem = {
      ...populated,
      title: "Role",
      company: null,
      location: null,
      is_remote: null,
      job_type: null,
      description: null,
      shift_type: null,
      salary_min: null,
      salary_max: null,
      salary_currency: null,
      source: "test",
      source_url: "https://example.test/job/1",
      company_logo: null,
      posted_at: null,
    } satisfies MatchItem;

    const skills = populated satisfies MatchSkillAnalysis;
    const explanation = {
      ...populated,
      llm_available: false,
      llm_confidence: null,
      llm_error: null,
      explanation: null,
    } satisfies MatchExplanation;

    expect([legacy, listItem, skills, explanation]).toHaveLength(4);
  });
});
