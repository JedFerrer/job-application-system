import { beforeEach, describe, expect, it, vi } from "vitest";

type Candidate = {
  attempt_id: string;
  application_id: number;
  role: string;
  company: string | null;
  expires_at: string;
  progress: string;
};

const expiry = new Date(Date.now() + 60_000).toISOString();
const candidateA: Candidate = {
  attempt_id: "attempt-a", application_id: 1, role: "Engineer", company: "Acme",
  expires_at: expiry, progress: "saved",
};
const candidateB: Candidate = { ...candidateA, attempt_id: "attempt-b", application_id: 2 };

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

async function setupWorker(resolved: () => Candidate[] = () => [candidateA]) {
  const storage: Record<string, unknown> = {};
  const urls = new Map<number, string>([
    [1, "https://boards.greenhouse.io/acme/jobs/123"],
    [2, "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890"],
  ]);
  const listener = { addListener: vi.fn() };
  const sendMessage = vi.fn().mockResolvedValue(undefined);
  const get = vi.fn(async (tabId: number) => ({ id: tabId, url: urls.get(tabId) }));
  const query = vi.fn(async (queryInfo: chrome.tabs.QueryInfo) => {
    if (queryInfo.active) return [{ id: 1, url: urls.get(1) }];
    return [...urls].map(([id, url]) => ({ id, url }));
  });
  (globalThis as { chrome: unknown }).chrome = {
    runtime: { onInstalled: listener, onStartup: listener, onMessage: listener, sendMessage },
    tabs: { onUpdated: listener, onRemoved: listener, query, get, sendMessage },
    storage: { session: {
      get: vi.fn(async (keys: string | string[] | null) => {
        if (keys === null) return { ...storage };
        const list = Array.isArray(keys) ? keys : [keys];
        return Object.fromEntries(list.filter((key) => key in storage).map((key) => [key, storage[key]]));
      }),
      set: vi.fn(async (values: Record<string, unknown>) => Object.assign(storage, values)),
      remove: vi.fn(async (keys: string | string[]) => {
        for (const key of Array.isArray(keys) ? keys : [keys]) delete storage[key];
      }),
    } },
  } as unknown as typeof chrome;
  (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/extension/applications/attempts?")) return json({ candidates: resolved() });
    return json({});
  }) as unknown as typeof fetch;
  vi.resetModules();
  const tabContext = await import("../../src/background/tab-context");
  const worker = await import("../../src/background/service-worker");
  return { storage, urls, get, sendMessage, tabContext, worker };
}

async function bind(
  worker: typeof import("../../src/background/service-worker"), tabId: number, candidate: Candidate,
) {
  await worker.handleRuntimeMessage({
    type: "BIND_ATTEMPT",
    payload: {
      attemptId: candidate.attempt_id, applicationId: candidate.application_id, jobId: 0,
      expiresAt: candidate.expires_at, role: candidate.role, company: candidate.company ?? undefined,
      progress: candidate.progress,
    },
  }, tabId);
}

beforeEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
});

describe("service-worker navigation state", () => {
  it("records a fresh opened event before binding and leaves no context when validation fails", async () => {
    const harness = await setupWorker();
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async () => json({ detail: "expired" }, 409)) as unknown as typeof fetch;

    await expect(bind(harness.worker, 1, candidateA)).rejects.toThrow();
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
  });

  it("rejects a forged bind for an unrelated current page without authorizing fill", async () => {
    const harness = await setupWorker();
    harness.urls.set(1, "https://boards.greenhouse.io/acme/jobs/456");
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async () => json({ detail: "page mismatch" }, 404)) as unknown as typeof fetch;

    await expect(bind(harness.worker, 1, candidateA)).rejects.toThrow();
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
    await expect(harness.worker.handleRuntimeMessage({
      type: "FILL_FORM", payload: { fields: [{ elementId: "first-name" }], mappings: [{ elementId: "first-name", action: "fill", value: "Owner", selectorPath: "#first-name" }] },
    }, 1)).rejects.toThrow("Select an application");
  });

  it("sends only the safe current-page identity before binding", async () => {
    const harness = await setupWorker();
    harness.urls.set(1, "https://www.indeed.com/viewjob?jk=abc123&token=secret&utm_source=campaign#apply");

    await bind(harness.worker, 1, candidateA);

    const bodies = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map(([, init]) => JSON.parse(String((init as RequestInit | undefined)?.body ?? "{}")));
    const opened = bodies.find((body) => body.event_type === "opened");
    expect(opened).toMatchObject({ attempt_id: candidateA.attempt_id, page_url: "https://www.indeed.com/viewjob?jk=abc123" });
    expect(opened.page_url).not.toContain("token");
    expect(opened.page_url).not.toContain("utm_");
    expect(opened.page_url).not.toContain("#");
    expect(await harness.tabContext.getAttemptContext(1)).toMatchObject({
      bindingState: "bound", boundPageUrl: "https://www.indeed.com/viewjob?jk=abc123",
    });
  });

  it("rejects fill after navigation before reconciliation can clear the stale binding", async () => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, {
      attemptId: candidateA.attempt_id, applicationId: candidateA.application_id, jobId: 0,
      expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", bindingState: "bound",
      boundPageUrl: "https://boards.greenhouse.io/acme/jobs/123",
    });
    harness.urls.set(1, "https://boards.greenhouse.io/acme/jobs/456");

    await expect(harness.worker.handleRuntimeMessage({
      type: "FILL_FORM", payload: { fields: [], mappings: [] },
    }, 1)).rejects.toThrow("Select an application");

    expect(harness.sendMessage).not.toHaveBeenCalled();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("rejects a same-host different job before dispatching fill", async () => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, {
      attemptId: candidateA.attempt_id, applicationId: candidateA.application_id, jobId: 0,
      expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", bindingState: "bound",
      boundPageUrl: "https://boards.greenhouse.io/acme/jobs/111",
    });
    harness.urls.set(1, "https://boards.greenhouse.io/acme/jobs/222");

    await expect(harness.worker.handleRuntimeMessage({
      type: "FILL_FORM", payload: { fields: [], mappings: [] },
    }, 1)).rejects.toThrow("Select an application");

    expect(harness.sendMessage).not.toHaveBeenCalled();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it.each([
    ["missing binding state", { boundPageUrl: "https://boards.greenhouse.io/acme/jobs/123" }],
    ["reconfirmation required", { bindingState: "reconfirmation_required" as const, boundPageUrl: "https://boards.greenhouse.io/acme/jobs/123" }],
    ["missing bound page URL", { bindingState: "bound" as const }],
  ])("fails closed for %s", async (_label, partialContext) => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, {
      attemptId: candidateA.attempt_id, applicationId: candidateA.application_id, jobId: 0,
      expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", ...partialContext,
    });

    await expect(harness.worker.handleRuntimeMessage({
      type: "FILL_FORM", payload: { fields: [], mappings: [] },
    }, 1)).rejects.toThrow("Select an application");

    expect(harness.sendMessage).not.toHaveBeenCalled();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("fills and logs when the current tab safe URL matches the bound page", async () => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, {
      attemptId: candidateA.attempt_id, applicationId: candidateA.application_id, jobId: 0,
      expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", bindingState: "bound",
      boundPageUrl: "https://boards.greenhouse.io/acme/jobs/123",
    });
    harness.urls.set(1, "https://boards.greenhouse.io/acme/jobs/123?tracking=x&session=secret#apply");
    harness.sendMessage.mockResolvedValue({ attempted: 1, filled: 1, skipped: 0, failed: 0 });

    await expect(harness.worker.handleRuntimeMessage({
      type: "FILL_FORM", payload: { fields: [{ elementId: "first-name" }], mappings: [{ elementId: "first-name", action: "fill", value: "Owner", selectorPath: "#first-name" }] },
    }, 1)).resolves.toMatchObject({ type: "AUTOFILL_COMPLETE", payload: { attempted: 1, filled: 1, failed: 0, skipped: 0 } });

    expect(harness.sendMessage).toHaveBeenCalledTimes(1);
    expect(harness.sendMessage).toHaveBeenCalledWith(1, expect.objectContaining({ type: "EXECUTE_FILL" }));
    expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining("/extension/forms/fill-log"), expect.anything());
  });

  it("authorizes with the server before dispatching a valid fill", async () => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, { attemptId: candidateA.attempt_id, applicationId: 1, jobId: 0, expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", bindingState: "bound", boundPageUrl: "https://boards.greenhouse.io/acme/jobs/123" });
    harness.sendMessage.mockResolvedValue({ attempted: 3, filled: 2, failed: 1, skipped: 2 });
    const calls: string[] = [];
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async (input) => { calls.push(String(input)); return json({}); }) as unknown as typeof fetch;

    const response = await harness.worker.handleRuntimeMessage({ type: "FILL_FORM", payload: { fields: Array.from({ length: 5 }, (_, index) => ({ elementId: `field-${index}` })), mappings: Array.from({ length: 5 }, (_, index) => ({ elementId: `field-${index}`, action: index < 3 ? "fill" as const : "skip" as const, value: index < 3 ? "value" : undefined, selectorPath: `#field-${index}` })) } }, 1);

    expect(calls[0]).toContain("/extension/profile/autofill");
    expect(harness.sendMessage).toHaveBeenCalledTimes(1);
    expect(response).toMatchObject({ type: "AUTOFILL_COMPLETE", payload: { attempted: 3, filled: 2, failed: 1, skipped: 2 } });
    const log = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.find(([url]) => String(url).includes("/extension/forms/fill-log"));
    expect(JSON.parse(String(log?.[1]?.body))).toMatchObject({ execution: { attempted: 3, filled: 2, failed: 1, skipped: 2 } });
  });

  it.each([[401, "revoked"], [500, "server failure"]])("fails closed before DOM dispatch when authorization returns %s", async (status, detail) => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, { attemptId: candidateA.attempt_id, applicationId: 1, jobId: 0, expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", bindingState: "bound", boundPageUrl: "https://boards.greenhouse.io/acme/jobs/123" });
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async () => json({ detail }, status)) as unknown as typeof fetch;

    await expect(harness.worker.handleRuntimeMessage({ type: "FILL_FORM", payload: { fields: [], mappings: [] } }, 1)).rejects.toThrow();
    expect(harness.sendMessage).not.toHaveBeenCalled();
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(([url]) => String(url).includes("/extension/forms/fill-log"))).toBe(false);
    if (status === 401) expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
  });

  it("requires explicit cross-host rebind before fill and records the new safe page", async () => {
    const harness = await setupWorker();
    const destination = "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890?token=secret#apply";
    await harness.tabContext.setAttemptContext(1, {
      attemptId: candidateA.attempt_id, applicationId: candidateA.application_id, jobId: 0,
      expiresAt: candidateA.expires_at, host: "jobs.lever.co", bindingState: "reconfirmation_required",
      boundPageUrl: "https://boards.greenhouse.io/acme/jobs/123",
    });
    harness.urls.set(1, destination);

    await expect(harness.worker.handleRuntimeMessage({
      type: "FILL_FORM", payload: { fields: [], mappings: [] },
    }, 1)).rejects.toThrow("Select an application");
    expect(harness.sendMessage).not.toHaveBeenCalled();

    await bind(harness.worker, 1, candidateA);
    const context = await harness.tabContext.getAttemptContext(1);
    expect(context).toMatchObject({
      bindingState: "bound", boundPageUrl: "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890",
    });

    harness.sendMessage.mockResolvedValue({ attempted: 0, filled: 0, skipped: 0, failed: 0 });
    await expect(harness.worker.handleRuntimeMessage({
      type: "FILL_FORM", payload: { fields: [], mappings: [] },
    }, 1)).resolves.toMatchObject({ type: "AUTOFILL_COMPLETE" });
  });

  it("uses a fresh event ID for every explicit bind", async () => {
    const harness = await setupWorker();

    await bind(harness.worker, 1, candidateA);
    await bind(harness.worker, 1, candidateA);

    const opened = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map(([, init]) => JSON.parse(String((init as RequestInit | undefined)?.body ?? "{}")))
      .filter((body) => body.event_type === "opened");
    expect(opened).toHaveLength(2);
    expect(opened[0].event_id).not.toBe(opened[1].event_id);
  });

  it("does not bind when the tab navigates while opened validation is in flight", async () => {
    const harness = await setupWorker();
    let release: ((response: Response) => void) | undefined;
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(() => new Promise<Response>((resolve) => {
      release = resolve;
    })) as unknown as typeof fetch;

    const pending = bind(harness.worker, 1, candidateA);
    await vi.waitFor(() => expect(release).toBeTypeOf("function"));
    harness.urls.set(1, "https://boards.greenhouse.io/acme/jobs/456");
    release?.(json({}));
    await pending;
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
  });

  it("reconfirms by recording a new opened event before returning to bound", async () => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, {
      attemptId: candidateA.attempt_id, applicationId: candidateA.application_id, jobId: 0,
      expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", bindingState: "reconfirmation_required",
    });
    await bind(harness.worker, 1, candidateA);
    expect(await harness.tabContext.getAttemptContext(1)).toMatchObject({ bindingState: "bound" });
    const bodies = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map(([, init]) => JSON.parse(String((init as RequestInit | undefined)?.body ?? "{}")));
    const opened = bodies.filter((body) => body.event_type === "opened");
    expect(opened).toHaveLength(1);
    expect(opened[0].page_url).toBe("https://boards.greenhouse.io/acme/jobs/123");
    expect(opened[0].event_id).toEqual(expect.any(String));
  });

  it.each(["updated-first", "page-context-first"])(
    "keeps a validated cross-host alias in reconfirmation state when %s", async (ordering) => {
      const harness = await setupWorker();
      await bind(harness.worker, 1, candidateA);
      await bind(harness.worker, 2, candidateB);
      await harness.tabContext.setDetectedFields(1, [{ elementId: "a" }]);
      await harness.tabContext.setDetectedFields(2, [{ elementId: "b" }]);
      const destination = "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890";
      harness.urls.set(1, destination);

      const pageContext = () => harness.worker.handleRuntimeMessage(
        { type: "PAGE_CONTEXT", payload: { pageUrl: destination } }, 1,
      );
      if (ordering === "updated-first") {
        await harness.worker.handleTabUpdated(1, { url: destination });
        await pageContext();
      } else {
        await pageContext();
        await harness.worker.handleTabUpdated(1, { url: destination });
      }

      expect(await harness.tabContext.getAttemptContext(1)).toMatchObject({
        attemptId: "attempt-a", bindingState: "reconfirmation_required",
      });
      expect(await harness.tabContext.getDetectedFields(1)).toEqual([]);
      expect(await harness.tabContext.getAttemptContext(2)).toMatchObject({ attemptId: "attempt-b" });
      expect(await harness.tabContext.getDetectedFields(2)).toEqual([{ elementId: "b" }]);
      await expect(harness.worker.handleRuntimeMessage({
        type: "FILL_FORM", payload: { fields: [], mappings: [] },
      }, 1)).rejects.toThrow("Select an application");
      await expect(harness.worker.handleRuntimeMessage({
        type: "MARK_APPLIED", payload: { jobUrl: destination },
      }, 1)).rejects.toThrow("Confirm the application");
      expect(harness.sendMessage).not.toHaveBeenCalledWith(1, expect.anything());
    },
  );

  it("retains a same-host validated binding and clears an unrelated page without touching Tab B", async () => {
    const resolver = vi.fn<() => Candidate[]>(() => [candidateA]);
    const harness = await setupWorker(resolver);
    await bind(harness.worker, 1, candidateA);
    await bind(harness.worker, 2, candidateB);
    const sameHost = "https://boards.greenhouse.io/acme/jobs/456";
    harness.urls.set(1, sameHost);
    await harness.worker.handleTabUpdated(1, { url: sameHost });
    expect(await harness.tabContext.getAttemptContext(1)).toMatchObject({ bindingState: "bound" });

    resolver.mockReturnValue([]);
    const unrelated = "https://boards.greenhouse.io/other/jobs/999";
    harness.urls.set(1, unrelated);
    await harness.worker.handleTabUpdated(1, { url: unrelated });
    await harness.worker.handleRuntimeMessage({ type: "PAGE_CONTEXT", payload: { pageUrl: unrelated } }, 1);
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
    expect(await harness.tabContext.getAttemptContext(2)).toMatchObject({ attemptId: "attempt-b" });
  });

  it("requires explicit binding for one or multiple resolved candidates", async () => {
    const resolver = vi.fn<() => Candidate[]>(() => [candidateA]);
    const harness = await setupWorker(resolver);
    const pageUrl = "https://boards.greenhouse.io/acme/jobs/123";
    const single = await harness.worker.handleRuntimeMessage({ type: "RESOLVE_ATTEMPTS", payload: { pageUrl } }, 1);
    expect(single).toMatchObject({ type: "ATTEMPT_CANDIDATES", payload: { candidates: [{ attemptId: "attempt-a" }] } });
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();

    resolver.mockReturnValue([candidateA, candidateB]);
    const multiple = await harness.worker.handleRuntimeMessage({ type: "RESOLVE_ATTEMPTS", payload: { pageUrl } }, 1);
    expect(multiple).toMatchObject({ type: "ATTEMPT_CANDIDATES", payload: { candidates: [{ attemptId: "attempt-a" }, { attemptId: "attempt-b" }] } });
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
    await bind(harness.worker, 1, candidateB);
    expect(await harness.tabContext.getAttemptContext(1)).toMatchObject({ attemptId: "attempt-b", bindingState: "bound" });
  });

  it("does not let an older resolution overwrite a newer tab context", async () => {
    const harness = await setupWorker();
    await bind(harness.worker, 1, candidateA);
    let resolveResponse: ((value: Response) => void) | undefined;
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(() => new Promise<Response>((resolve) => {
      resolveResponse = resolve;
    })) as unknown as typeof fetch;
    const oldDestination = "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890";
    harness.urls.set(1, oldDestination);
    const pending = harness.worker.handleTabUpdated(1, { url: oldDestination });
    await Promise.resolve();

    const newDestination = "https://boards.greenhouse.io/acme/jobs/456";
    harness.urls.set(1, newDestination);
    await harness.tabContext.setAttemptContext(1, {
      attemptId: "attempt-b", applicationId: 2, jobId: 0, expiresAt: expiry,
      host: "boards.greenhouse.io", bindingState: "bound",
    });
    resolveResponse?.(json({ candidates: [candidateA] }));
    await pending;
    expect(await harness.tabContext.getAttemptContext(1)).toMatchObject({
      attemptId: "attempt-b", bindingState: "bound",
    });
  });

  it("clears only the closed or unsupported tab and clears stale restored bindings", async () => {
    const harness = await setupWorker();
    await bind(harness.worker, 1, candidateA);
    await bind(harness.worker, 2, candidateB);
    await harness.worker.handleTabRemoved(1);
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
    expect(await harness.tabContext.getAttemptContext(2)).toMatchObject({ attemptId: "attempt-b" });

    await bind(harness.worker, 1, candidateA);
    harness.urls.set(1, "chrome://newtab/");
    harness.get.mockClear();
    await harness.worker.handleTabUpdated(1, { url: "chrome://newtab/" });
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
    expect(harness.get).not.toHaveBeenCalledWith(1);

    await bind(harness.worker, 1, candidateA);
    harness.urls.set(1, "chrome://newtab/");
    await harness.worker.restoreTabContexts();
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
  });
});

describe("api-client production cleanup", () => {
  it("propagates a localhost:8000 network failure without requesting localhost:8001", async () => {
    await setupWorker();
    const api = await import("../../src/background/api-client");
    const networkFailure = new TypeError("Network request failed");
    const fetchMock = vi.fn(async () => { throw networkFailure; });
    (globalThis as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;

    await expect(api.apiRequest("/extension/test", { skipAuth: true })).rejects.toBe(networkFailure);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://localhost:8000/api/v1/extension/test");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("localhost:8001"))).toBe(false);
  });

  it("retains contexts after a successful refresh and clears tokens and every context after refresh failure", async () => {
    const success = await setupWorker();
    const api = await import("../../src/background/api-client");
    await success.tabContext.setAttemptContext(1, {
      attemptId: "attempt-a", applicationId: 1, jobId: 0, expiresAt: expiry, host: "boards.greenhouse.io",
    });
    await api.storeTokens("old", "refresh", 60);
    let request = 0;
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async () => {
      request += 1;
      if (request === 1) return json({}, 401);
      if (request === 2) return json({ access_token: "new", refresh_token: "new-refresh", expires_in: 60 });
      return json({ ok: true });
    }) as unknown as typeof fetch;
    await api.apiRequest("/extension/test");
    expect(await success.tabContext.getAttemptContext(1)).toMatchObject({ attemptId: "attempt-a" });

    const failure = await setupWorker();
    const failedApi = await import("../../src/background/api-client");
    await failure.tabContext.setAttemptContext(1, {
      attemptId: "attempt-a", applicationId: 1, jobId: 0, expiresAt: expiry, host: "boards.greenhouse.io",
    });
    await failure.tabContext.setAttemptContext(2, {
      attemptId: "attempt-b", applicationId: 2, jobId: 0, expiresAt: expiry, host: "jobs.lever.co",
    });
    await failure.tabContext.setDetectedFields(1, [{ elementId: "a" }]);
    await failedApi.storeTokens("old", "refresh", 60);
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async () => json({}, 401)) as unknown as typeof fetch;
    await expect(failedApi.apiRequest("/extension/test")).rejects.toThrow("Authentication expired");
    expect(await failure.tabContext.getAttemptContext(1)).toBeNull();
    expect(await failure.tabContext.getAttemptContext(2)).toBeNull();
    expect(await failure.tabContext.getDetectedFields(1)).toBeUndefined();
    expect(failure.storage.access_token).toBeUndefined();
    expect(failure.storage.refresh_token).toBeUndefined();
  });
});

describe("content submission event validation", () => {
  const confirmation = {
    type: "CONFIRMATION_DETECTED" as const,
    payload: {
      pageUrl: "https://boards.greenhouse.io/acme/jobs/123",
      confirmationText: "Application successfully submitted",
      eventId: "confirmation-event-id",
    },
  };

  it("forwards a supplied confirmation event ID unchanged, including replay", async () => {
    const harness = await setupWorker();
    await bind(harness.worker, 1, candidateA);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockClear();

    await harness.worker.handleRuntimeMessage(confirmation, 1);
    await harness.worker.handleRuntimeMessage(confirmation, 1);

    const marks = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
      .filter(([url]) => String(url).includes("/extension/applications/mark-applied"))
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(marks).toHaveLength(2);
    expect(marks).toEqual(expect.arrayContaining([
      expect.objectContaining({ event_id: "confirmation-event-id", attempt_id: "attempt-a" }),
    ]));
    expect(marks[0].event_id).toBe(marks[1].event_id);
  });

  it("rejects confirmation without a bound attempt context", async () => {
    const harness = await setupWorker();
    await expect(harness.worker.handleRuntimeMessage(confirmation, 1)).rejects.toThrow("Confirm the application");
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("rejects reconfirmation-required context without marking applied", async () => {
    const harness = await setupWorker();
    await harness.tabContext.setAttemptContext(1, {
      attemptId: candidateA.attempt_id, applicationId: candidateA.application_id, jobId: 0,
      expiresAt: candidateA.expires_at, host: "boards.greenhouse.io", bindingState: "reconfirmation_required",
    });
    await expect(harness.worker.handleRuntimeMessage(confirmation, 1)).rejects.toThrow("Confirm the application");
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("rejects a stale content page URL before any backend mutation", async () => {
    const harness = await setupWorker();
    await bind(harness.worker, 1, candidateA);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockClear();
    harness.urls.set(1, "https://boards.greenhouse.io/acme/jobs/456");

    await expect(harness.worker.handleRuntimeMessage(confirmation, 1)).rejects.toThrow("page changed");
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("does not mark applied when the authenticated confirmation request is revoked", async () => {
    const harness = await setupWorker();
    await bind(harness.worker, 1, candidateA);
    (globalThis as { fetch: typeof fetch }).fetch = vi.fn(async (input) => {
      if (String(input).includes("/extension/applications/attempts?")) return json({ candidates: [candidateA] });
      return json({ detail: "revoked" }, 401);
    }) as unknown as typeof fetch;

    await expect(harness.worker.handleRuntimeMessage(confirmation, 1)).rejects.toThrow("Authentication expired");
    expect(await harness.tabContext.getAttemptContext(1)).toBeNull();
  });
});
