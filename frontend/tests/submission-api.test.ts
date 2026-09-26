import { afterEach, describe, expect, it, vi } from "vitest";
import { applicationsApi, setAccessToken } from "@/lib/api";

afterEach(() => { vi.unstubAllGlobals(); setAccessToken(null); });

describe("submission and interview API", () => {
  it("sends the same confirmation key and typed payload on retry", async () => {
    const payload = { material_choice: "materials_unknown" as const, note: "Sent from a different device" };
    const response = { id: 11, submitted_at_is_user_reported: false };
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(applicationsApi.confirmSubmission(7, payload, "attempt-key")).rejects.toThrow("network");
    expect(await applicationsApi.confirmSubmission(7, payload, "attempt-key")).toMatchObject(response);
    expect(fetchMock.mock.calls.map((call) => (call[1] as RequestInit).headers)).toEqual([
      expect.objectContaining({ "Idempotency-Key": "attempt-key" }), expect.objectContaining({ "Idempotency-Key": "attempt-key" }),
    ]);
    expect(JSON.parse((fetchMock.mock.calls[1]![1] as RequestInit).body as string)).toEqual(payload);
  });

  it("uses list cursors, preserves provenance and submission IDs, and accepts empty delete", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: "more" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 11, submitted_at_is_user_reported: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ application_id: 7, items: [{ submission_id: 11 }], next_cursor: null }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    expect((await applicationsApi.listSubmissions(7, { limit: 10, cursor: "a/b" })).next_cursor).toBe("more");
    expect((fetchMock.mock.calls[0]![0] as string)).toContain("limit=10&cursor=a%2Fb");
    expect((await applicationsApi.getSubmission(7, 11)).submitted_at_is_user_reported).toBe(true);
    expect((await applicationsApi.getTimeline(7)).items[0]?.submission_id).toBe(11);
    expect(await applicationsApi.deleteSubmission(7, 11)).toBeUndefined();
  });

  it("sends interview context and safely parses validation errors", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ source_label: "current_profile_fallback", cached: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ loc: ["body", "question_count"], msg: "must be at most 20" }] }), { status: 422 }));
    vi.stubGlobal("fetch", fetchMock);
    const payload = { submission_id: null, current_profile_fallback: true, stage: "technical" as const, focus_areas: [], question_count: 8, force_refresh: true };
    expect((await applicationsApi.prepareInterview(7, payload)).source_label).toBe("current_profile_fallback");
    expect(JSON.parse((fetchMock.mock.calls[0]![1] as RequestInit).body as string)).toEqual(payload);
    await expect(applicationsApi.prepareInterview(7, payload)).rejects.toMatchObject({ status: 422, message: "question_count: must be at most 20" });
  });
});
