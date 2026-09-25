import { describe, expect, it, vi } from "vitest";
import { createRetryableLoader } from "@/lib/retryable-loader";
import jsPDF from "jspdf";

describe("retryable PDF font load cache", () => {
  it("shares an in-flight load, clears a rejection, and retains success", async () => {
    let rejectFirst!: (error: Error) => void;
    let resolveSecond!: (value: string) => void;
    const load = vi.fn()
      .mockImplementationOnce(() => new Promise<string>((_, reject) => { rejectFirst = reject; }))
      .mockImplementationOnce(() => new Promise<string>((resolve) => { resolveSecond = resolve; }));
    const fonts = createRetryableLoader(load);
    const exportPdf = async () => { await fonts(); return new jsPDF().output("blob"); };

    const first = exportPdf();
    const concurrent = fonts();
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);
    rejectFirst(new Error("chunk unavailable"));
    await expect(first).rejects.toThrow("chunk unavailable");
    await expect(concurrent).rejects.toThrow("chunk unavailable");

    const retry = exportPdf();
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(2);
    resolveSecond("loaded fonts");
    const pdf = await retry;
    expect(pdf.type).toBe("application/pdf");
    expect(pdf.size).toBeGreaterThan(0);
    const cached = fonts();
    await expect(cached).resolves.toBe("loaded fonts");
    expect(fonts()).toBe(cached);
    expect(load).toHaveBeenCalledTimes(2);
  });
});
