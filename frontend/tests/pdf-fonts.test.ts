import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

const fonts = resolve(dirname(fileURLToPath(import.meta.url)), "../public/fonts");
const regular = readFileSync(resolve(fonts, "NotoSans-Regular.ttf"));
const bold = readFileSync(resolve(fonts, "NotoSans-Bold.ttf"));

function response(bytes: Buffer, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    arrayBuffer: async () => Uint8Array.from(bytes).buffer,
  } as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("static PDF fonts", () => {
  it("retries a partial HTTP failure on the same module, then caches successful bytes and builds a PDF", async () => {
    vi.resetModules();
    const requests: Array<{ url: string; cache: RequestCache | undefined }> = [];
    let failBold = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      requests.push({ url, cache: init.cache });
      if (url.endsWith("Bold.ttf") && failBold) return response(bold, 503);
      return response(url.endsWith("Bold.ttf") ? bold : regular);
    }));
    const { loadPdfFonts } = await import("@/lib/pdf-fonts");
    const { buildCoverLetterPdf, downloadPdf } = await import("@/lib/cover-letter-export");

    await expect(buildCoverLetterPdf("José café ₱ €")).rejects.toThrow("503");
    expect(requests.map(({ url }) => url)).toEqual([
      "/fonts/NotoSans-Regular.ttf", "/fonts/NotoSans-Bold.ttf",
    ]);
    failBold = false;
    const loaded = await loadPdfFonts();
    expect(requests).toHaveLength(4);
    expect(requests.every(({ cache }) => cache === "no-store")).toBe(true);
    expect(Buffer.from(loaded.regular, "base64")).toEqual(regular);
    expect(Buffer.from(loaded.bold, "base64")).toEqual(bold);
    const built = await buildCoverLetterPdf("José café ₱ €");
    expect(built.blob.size).toBeGreaterThan(0);
    expect(built.exporterVersion).toBe("cover-letter-pdf-v2");
    expect(requests).toHaveLength(4);

    const createObjectURL = vi.fn(() => "blob:recovered-pdf");
    const revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.href).toBe("blob:recovered-pdf");
      expect(this.download).toBe("recovered.pdf");
    });
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    await downloadPdf("José café ₱ €", "recovered");
    expect(createObjectURL).toHaveBeenCalledWith(expect.objectContaining({ type: "application/pdf" }));
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:recovered-pdf");
    expect(requests).toHaveLength(4);
    click.mockRestore();
  }, 30_000);

  it("shares one pair of pending font requests across concurrent callers", async () => {
    vi.resetModules();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const fetchFont = vi.fn(async (url: string) => {
      await gate;
      return response(url.endsWith("Bold.ttf") ? bold : regular);
    });
    vi.stubGlobal("fetch", fetchFont);
    const { loadPdfFonts } = await import("@/lib/pdf-fonts");
    const first = loadPdfFonts();
    const second = loadPdfFonts();
    await Promise.resolve();
    expect(fetchFont).toHaveBeenCalledTimes(2);
    release();
    expect(await first).toBe(await second);
    expect(await loadPdfFonts()).toBe(await first);
    expect(fetchFont).toHaveBeenCalledTimes(2);
  });
});
