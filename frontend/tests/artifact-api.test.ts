import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, applicationsApi, setAccessToken } from "@/lib/api";
import { buildCoverLetterPdf } from "@/lib/cover-letter-export";
import { createHash } from "node:crypto";

const artifact = { id: 9, package_id: 2, document_type: "cover_letter", format: "pdf", validation_state: "queued", retry_after: "2026-09-24T12:01:00Z" };

afterEach(() => { vi.unstubAllGlobals(); setAccessToken(null); });

describe("artifact API", () => {
  it("uploads the exact builder bytes and required multipart metadata", async () => {
    const built = buildCoverLetterPdf("Hello from the frozen letter", "cover_letter_Role");
    const requests: FormData[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      requests.push(init.body as FormData);
      return new Response(JSON.stringify({ artifact, existing: false }), { status: 202, headers: { "Content-Type": "application/json" } });
    }));
    const result = await applicationsApi.uploadArtifact(1, 2, built.blob, built.filename, "cover_letter", built.format, built.exporterVersion);
    expect(result.existing).toBe(false);
    expect(requests).toHaveLength(1);
    const form = requests[0]!;
    const file = form.get("file") as File;
    expect(file.name).toBe(built.filename);
    expect(Array.from(new Uint8Array(await file.arrayBuffer()))).toEqual(Array.from(new Uint8Array(await built.blob.arrayBuffer())));
    expect(form.get("document_type")).toBe("cover_letter");
    expect(form.get("format")).toBe("pdf");
    expect(form.get("exporter_version")).toBe("cover-letter-pdf-v1");
  });

  it("handles duplicate, detail, retry, and empty delete responses", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ artifact, existing: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(artifact), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ artifact, existing: false }), { status: 202 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const built = new Blob(["%PDF-1.4\n"], { type: "application/pdf" });
    expect((await applicationsApi.uploadArtifact(1, 2, built, "letter.pdf", "cover_letter", "pdf", "cover-letter-pdf-v1")).existing).toBe(true);
    expect((await applicationsApi.getArtifact(1, 9)).retry_after).toBe(artifact.retry_after);
    expect((await applicationsApi.retryArtifactValidation(1, 9)).artifact.id).toBe(9);
    expect(await applicationsApi.deleteArtifact(1, 9)).toBeUndefined();
  });

  it("returns binary bytes and a safe server filename, without JSON parsing", async () => {
    const bytes = new Uint8Array([0, 255, 37, 80, 68, 70, 45, 49, 46, 55, 10, 128, 1, 42]);
    const expectedSha256 = "4b3bbaece915053bc77927b626d7c71b532da93e6d17ccc71701fe2d6204c92c";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes, { headers: { "Content-Disposition": 'attachment; filename="stored.pdf"', "Content-Type": "application/pdf" } })));
    const result = await applicationsApi.downloadArtifact(1, 9, "fallback.pdf");
    expect(result.filename).toBe("stored.pdf");
    const downloaded = new Uint8Array(await result.blob.arrayBuffer());
    expect(Array.from(downloaded)).toEqual(Array.from(bytes));
    expect(createHash("sha256").update(downloaded).digest("hex")).toBe(expectedSha256);
  });

  it("sanitizes an attachment filename and falls back when it is missing", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(new Uint8Array([37, 80, 68, 70, 45]), { headers: { "Content-Disposition": 'attachment; filename="../stored.pdf"' } }))
      .mockResolvedValueOnce(new Response(new Uint8Array([37, 80, 68, 70, 45])));
    vi.stubGlobal("fetch", fetchMock);
    expect((await applicationsApi.downloadArtifact(1, 9, "fallback.pdf")).filename).toBe("stored.pdf");
    expect((await applicationsApi.downloadArtifact(1, 9, "fallback.pdf")).filename).toBe("fallback.pdf");
  });

  it("refreshes an expired session before downloading stored bytes", async () => {
    setAccessToken("expired-token");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Expired" }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: "fresh-token", token_type: "bearer" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(new Uint8Array([37, 80, 68, 70, 45]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await applicationsApi.downloadArtifact(1, 9, "fallback.pdf");
    expect(result.filename).toBe("fallback.pdf");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((fetchMock.mock.calls[2]![1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer fresh-token" });
  });

  it("rejects JSON download errors instead of returning them as files", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Artifact not found" }), { status: 404, headers: { "Content-Type": "application/json" } })));
    await expect(applicationsApi.downloadArtifact(1, 9)).rejects.toMatchObject({ status: 404, detail: "Artifact not found" } satisfies Partial<ApiError>);
  });
});
