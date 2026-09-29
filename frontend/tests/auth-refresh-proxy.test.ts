import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "@/app/api/backend/[...path]/route";

describe("auth refresh proxy diagnostics", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const request = () => POST(
    new NextRequest("http://localhost:3000/api/backend/auth/refresh", { method: "POST" }),
    { params: Promise.resolve({ path: ["auth", "refresh"] }) },
  );

  it("keeps the 502 response and logs only safe transport fields", async () => {
    const error = Object.assign(new TypeError("secret token must not be logged"), {
      cause: { code: "ECONNRESET", privateValue: "secret cookie" },
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(error));
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});

    const response = await request();

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    expect(warning).toHaveBeenCalledWith("Auth refresh proxy request failed", {
      elapsedMs: expect.any(Number),
      errorName: "TypeError",
      errorCode: "ECONNRESET",
    });
    expect(JSON.stringify(warning.mock.calls)).not.toContain("secret");
  });

  it("omits unknown error names and codes even when they look like safe identifiers", async () => {
    const error = Object.assign(new Error("secret message"), {
      name: "SecretAccessToken123",
      cause: { code: "SecretRefreshToken456" },
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(error));
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});

    const response = await request();

    expect(response.status).toBe(502);
    expect(warning).toHaveBeenCalledWith("Auth refresh proxy request failed", {
      elapsedMs: expect.any(Number),
      errorName: undefined,
      errorCode: undefined,
    });
    expect(JSON.stringify(warning.mock.calls)).not.toContain("Secret");
  });

  it("preserves an upstream error status and logs no response body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("sensitive body", { status: 503 })));
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});

    const response = await request();

    expect(response.status).toBe(503);
    expect(await response.text()).toBe("sensitive body");
    expect(warning).toHaveBeenCalledWith("Auth refresh proxy returned an error", {
      elapsedMs: expect.any(Number),
      status: 503,
    });
    expect(JSON.stringify(warning.mock.calls)).not.toContain("sensitive body");
  });
});
