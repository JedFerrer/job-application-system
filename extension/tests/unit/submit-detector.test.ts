import { beforeAll, beforeEach, afterAll, describe, expect, it, vi } from "vitest";

const pageUrl = "/acme/jobs/123";
let sendMessage: ReturnType<typeof vi.fn>;

function submit(): void {
  document.querySelector<HTMLButtonElement>("button")!.click();
}

function confirmation(): void {
  const element = document.createElement("div");
  element.className = "confirmation";
  element.setAttribute("role", "alert");
  element.textContent = "Application successfully submitted";
  document.body.appendChild(element);
}

function messages(type: "SUBMIT_CLICKED" | "CONFIRMATION_DETECTED") {
  return sendMessage.mock.calls.map(([message]) => message).filter((message) => message.type === type);
}

describe("submit detector late confirmation", () => {
  beforeAll(async () => {
    sendMessage = vi.fn().mockResolvedValue({ type: "EVENT_RECORDED" });
    (globalThis as { chrome: unknown }).chrome = { runtime: { sendMessage } };
    vi.spyOn(globalThis.crypto, "randomUUID").mockImplementation(() => `event-${vi.getTimerCount()}-${Math.random()}`);
    vi.useFakeTimers();
    await import("../../src/content/submit-detector").then(({ initSubmitDetector }) => initSubmitDetector());
  });

  beforeEach(() => {
    document.body.innerHTML = `<button type="submit">Submit Application</button>`;
    history.replaceState({}, "", pageUrl);
    sendMessage.mockClear();
  });

  afterAll(() => {
    window.dispatchEvent(new Event("pagehide"));
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("preserves immediate confirmation without recording unconfirmed", async () => {
    submit();
    confirmation();
    await vi.advanceTimersByTimeAsync(3_000);

    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(1);
    expect(messages("SUBMIT_CLICKED")).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(1);
  });

  it("detects dynamically inserted confirmation exactly once after unconfirmed", async () => {
    submit();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(messages("SUBMIT_CLICKED")).toHaveLength(1);

    confirmation();
    await Promise.resolve();
    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(1);
    const eventId = messages("CONFIRMATION_DETECTED")[0].payload.eventId;

    document.body.appendChild(document.createElement("p"));
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(1);
    expect(messages("CONFIRMATION_DETECTED")[0].payload.eventId).toBe(eventId);
  });

  it("closes the scan-to-observer race with an observe-then-rescan", async () => {
    const NativeMutationObserver = globalThis.MutationObserver;
    const originalObserve = NativeMutationObserver.prototype.observe;
    let inserted = false;
    NativeMutationObserver.prototype.observe = function observeAfterGap(target: Node, options?: MutationObserverInit): void {
      if (!inserted) {
        inserted = true;
        // Insert before registration: this mutation cannot wake the observer.
        confirmation();
      }
      originalObserve.call(this, target, options);
    };
    try {
      submit();
      await vi.advanceTimersByTimeAsync(3_000);

      expect(inserted).toBe(true);
      expect(messages("CONFIRMATION_DETECTED")).toHaveLength(1);
      expect(messages("SUBMIT_CLICKED")).toHaveLength(0);
      expect(messages("CONFIRMATION_DETECTED")[0].payload.pageUrl).toBe(window.location.href);

      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(30_000);
      expect(messages("CONFIRMATION_DETECTED")).toHaveLength(1);
    } finally {
      NativeMutationObserver.prototype.observe = originalObserve;
    }
  });

  it("expires an unconfirmed watch without fabricating confirmation", async () => {
    submit();
    await vi.advanceTimersByTimeAsync(33_000);
    expect(messages("SUBMIT_CLICKED")).toHaveLength(1);
    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(0);

    confirmation();
    await Promise.resolve();
    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(0);
  });

  it("cleans up a pending submit when the page URL changes", async () => {
    submit();
    await vi.advanceTimersByTimeAsync(3_000);
    history.pushState({}, "", "/acme/jobs/456");
    confirmation();
    await Promise.resolve();

    expect(messages("SUBMIT_CLICKED")).toHaveLength(1);
    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(0);
  });

  it("supersedes the old watch when the user submits again", async () => {
    submit();
    await vi.advanceTimersByTimeAsync(3_000);
    history.pushState({}, "", "/acme/jobs/456");
    submit();
    await vi.advanceTimersByTimeAsync(3_000);
    confirmation();
    await Promise.resolve();

    expect(messages("SUBMIT_CLICKED")).toHaveLength(2);
    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(1);
    expect(messages("CONFIRMATION_DETECTED")[0].payload.pageUrl).toContain("/456");
  });

  it("cleans up a pending watch on pagehide", async () => {
    submit();
    await vi.advanceTimersByTimeAsync(3_000);
    window.dispatchEvent(new Event("pagehide"));
    confirmation();
    await Promise.resolve();

    expect(messages("CONFIRMATION_DETECTED")).toHaveLength(0);
  });

});
