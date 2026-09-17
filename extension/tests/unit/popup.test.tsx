import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../../src/popup/App";
import AutofillPreviewPage from "../../src/popup/pages/AutofillPreviewPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const reconfirmationContext = {
  attemptId: "attempt-a",
  applicationId: 1,
  jobId: 0,
  expiresAt: new Date(Date.now() + 60_000).toISOString(),
  host: "boards.greenhouse.io",
  bindingState: "reconfirmation_required" as const,
};

const boundContext = { ...reconfirmationContext, bindingState: "bound" as const };
const restoredBoundContext = { ...boundContext, attemptId: "attempt-restored", applicationId: 2 };
const profile = { personal: { first_name: "Owner", last_name: "Example", email: "owner@example.test", phone: null, city: null, country: null }, links: { linkedin: null, github: null, portfolio: null, website: null }, work: { current_title: null, current_company: "Acme", years_experience: null, expected_salary: "100000" }, authorization: { requires_sponsorship: null, authorized_to_work: null }, education: [], experience: [], skills: [] };
const detectedFields = [
  { elementId: "first", tagName: "input", inputType: "text", label: "First Name", visible: true, selectorPath: "#first" },
  { elementId: "salary", tagName: "input", inputType: "text", label: "Expected Salary", visible: true, selectorPath: "#salary" },
  { elementId: "veteran", tagName: "select", inputType: "select", label: "Veteran Status", visible: true, selectorPath: "#veteran" },
  { elementId: "resume", tagName: "input", inputType: "file", label: "Resume", visible: true, selectorPath: "#resume" },
  { elementId: "unknown", tagName: "input", inputType: "text", label: "Unmapped question", visible: false, selectorPath: "#unknown" },
];

let container: HTMLDivElement | undefined;
let root: Root | undefined;

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
});

async function renderReconfirmation() {
  const sendMessage = vi.fn(async (message: { type: string }) => {
    if (message.type === "GET_SESSION_STATE") {
      return { type: "SESSION_STATE", payload: { connected: true, user: { id: 1, email: "owner@example.test", name: "Owner" } } };
    }
    if (message.type === "GET_TAB_CONTEXT") {
      return { type: "TAB_CONTEXT", payload: { context: reconfirmationContext } };
    }
    if (message.type === "BIND_ATTEMPT") {
      return { type: "TAB_CONTEXT", payload: { context: { ...reconfirmationContext, bindingState: "bound" as const } } };
    }
    return { type: "TAB_CONTEXT", payload: { context: null } };
  });
  (globalThis as { chrome: unknown }).chrome = {
    runtime: { sendMessage, onMessage: { addListener: vi.fn(), removeListener: vi.fn() } },
    tabs: { query: vi.fn() },
  } as unknown as typeof chrome;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<App />);
    await Promise.resolve();
    await Promise.resolve();
  });
  return sendMessage;
}

async function renderApp({ context = boundContext, fields = detectedFields, fill }: { context?: typeof boundContext | typeof reconfirmationContext | null; fields?: typeof detectedFields; fill?: () => Promise<unknown> | unknown } = {}) {
  const sendMessage = vi.fn(async (message: { type: string }) => {
    if (message.type === "GET_SESSION_STATE") return { type: "SESSION_STATE", payload: { connected: true, user: { id: 1, email: "owner@example.test", name: "Owner" } } };
    if (message.type === "GET_TAB_CONTEXT") return { type: "TAB_CONTEXT", payload: { context, fields } };
    if (message.type === "GET_AUTOFILL_PROFILE") return { type: "AUTOFILL_PROFILE", payload: profile };
    if (message.type === "FILL_FORM") return fill ? fill() : { type: "AUTOFILL_COMPLETE", payload: { attempted: 3, filled: 2, failed: 1, skipped: 0 } };
    return { type: "TAB_CONTEXT", payload: { context: null } };
  });
  (globalThis as { chrome: unknown }).chrome = { runtime: { sendMessage, onMessage: { addListener: vi.fn(), removeListener: vi.fn() } }, tabs: { query: vi.fn() }, storage: { onChanged: { addListener: vi.fn(), removeListener: vi.fn() } } } as unknown as typeof chrome;
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => { root?.render(<App />); await Promise.resolve(); await Promise.resolve(); });
  return sendMessage;
}

async function clickButton(label: string) {
  await act(async () => { Array.from(container?.querySelectorAll("button") ?? []).find((button) => button.textContent === label)?.click(); await Promise.resolve(); await Promise.resolve(); });
}

describe("popup reconfirmation", () => {
  it("only binds after the user explicitly confirms the recognized application", async () => {
    const sendMessage = await renderReconfirmation();
    expect(container?.textContent).toContain("Confirm before filling or tracking continues");
    expect(sendMessage.mock.calls.map(([message]) => message.type)).not.toContain("BIND_ATTEMPT");

    await act(async () => {
      (container?.querySelector("button") as HTMLButtonElement).click();
      await Promise.resolve();
    });

    expect(sendMessage).toHaveBeenCalledWith({ type: "BIND_ATTEMPT", payload: reconfirmationContext });
    expect(container?.textContent).toContain("This tab is bound to application #1.");
  });

  it("does not expose review or fill paths from App while reconfirmation is required", async () => {
    const sendMessage = await renderApp({ context: reconfirmationContext });
    expect(container?.textContent).not.toContain("Review autofill");
    expect(container?.textContent).not.toContain("Fill Safe Fields");
    expect(sendMessage.mock.calls.map(([message]) => message.type)).not.toContain("GET_AUTOFILL_PROFILE");
  });
});

describe("popup App autofill flow", () => {
  it("restores a bound tab with stored detected fields and exposes review", async () => {
    const sendMessage = await renderApp();
    expect(container?.textContent).toContain("This tab is bound to application #1.");
    expect(container?.textContent).toContain("Review autofill");
    expect(sendMessage.mock.calls.map(([message]) => message.type)).toEqual(["GET_SESSION_STATE", "GET_TAB_CONTEXT"]);
  });

  it("does not expose review for a bound tab with no detected fields", async () => {
    await renderApp({ fields: [] });
    expect(container?.textContent).toContain("This tab is bound to application #1.");
    expect(container?.textContent).not.toContain("Review autofill");
  });

  it("re-reads authoritative context before loading the profile and renders real mappings", async () => {
    const sendMessage = await renderApp();
    await clickButton("Review autofill");
    const types = sendMessage.mock.calls.map(([message]) => message.type);
    expect(types).toEqual(["GET_SESSION_STATE", "GET_TAB_CONTEXT", "GET_TAB_CONTEXT", "GET_AUTOFILL_PROFILE"]);
    expect(container?.textContent).toContain("Autofill Preview");
    expect(container?.textContent).toContain("1 safe");
    expect(types).not.toContain("FILL_FORM");
    await clickButton("Fill Safe Fields");
    const payload = sendMessage.mock.calls.find(([message]) => message.type === "FILL_FORM")?.[0].payload;
    expect(payload.mappings).toHaveLength(detectedFields.length);
    expect(payload.mappings).toEqual(expect.arrayContaining([
      expect.objectContaining({ elementId: "first", action: "fill", selectorPath: "#first", profilePath: "personal.first_name" }),
      expect.objectContaining({ elementId: "salary", action: "review", selectorPath: "#salary", profilePath: "work.expected_salary" }),
      expect.objectContaining({ elementId: "veteran", action: "skip", selectorPath: "#veteran", profilePath: null }),
    ]));
  });

  async function reviewWithAuthoritativeContext(authoritativeContext: typeof boundContext | typeof reconfirmationContext) {
    let tabContextReads = 0;
    const tabContexts = [boundContext, authoritativeContext, restoredBoundContext];
    const returnedContexts: Array<typeof boundContext | typeof reconfirmationContext> = [];
    const sendMessage = vi.fn(async (message: { type: string }) => {
      if (message.type === "GET_SESSION_STATE") {
        return { type: "SESSION_STATE", payload: { connected: true, user: { id: 1, email: "owner@example.test", name: "Owner" } } };
      }
      if (message.type === "GET_TAB_CONTEXT") {
        const context = tabContexts[tabContextReads++];
        returnedContexts.push(context);
        return {
          type: "TAB_CONTEXT",
          payload: {
            context,
            fields: detectedFields,
          },
        };
      }
      if (message.type === "GET_AUTOFILL_PROFILE") return { type: "AUTOFILL_PROFILE", payload: profile };
      return { type: "TAB_CONTEXT", payload: { context: null } };
    });
    (globalThis as { chrome: unknown }).chrome = {
      runtime: { sendMessage, onMessage: { addListener: vi.fn(), removeListener: vi.fn() } },
      tabs: { query: vi.fn() },
      storage: { onChanged: { addListener: vi.fn(), removeListener: vi.fn() } },
    } as unknown as typeof chrome;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => { root?.render(<App />); await Promise.resolve(); await Promise.resolve(); });

    expect(container?.textContent).toContain("Review autofill");
    await clickButton("Review autofill");
    return { sendMessage, tabContextReads, returnedContexts };
  }

  it("loads the profile and reaches preview when the second authoritative context remains eligible", async () => {
    const { sendMessage, tabContextReads, returnedContexts } = await reviewWithAuthoritativeContext(boundContext);

    const types = sendMessage.mock.calls.map(([message]) => message.type);
    expect(types).toEqual(["GET_SESSION_STATE", "GET_TAB_CONTEXT", "GET_TAB_CONTEXT", "GET_AUTOFILL_PROFILE"]);
    expect(tabContextReads).toBe(2);
    expect(returnedContexts).toEqual([boundContext, boundContext]);
    expect(types.filter((type) => type === "GET_AUTOFILL_PROFILE")).toHaveLength(1);
    expect(container?.textContent).toContain("Autofill Preview");
  });

  it("stops before profile loading when the second authoritative context requires reconfirmation", async () => {
    const { sendMessage, tabContextReads, returnedContexts } = await reviewWithAuthoritativeContext(reconfirmationContext);

    const types = sendMessage.mock.calls.map(([message]) => message.type);
    expect(types).toEqual(["GET_SESSION_STATE", "GET_TAB_CONTEXT", "GET_TAB_CONTEXT", "GET_SESSION_STATE", "GET_TAB_CONTEXT"]);
    expect(tabContextReads).toBe(3);
    expect(returnedContexts).toEqual([boundContext, reconfirmationContext, restoredBoundContext]);
    expect(types.filter((type) => type === "GET_AUTOFILL_PROFILE")).toHaveLength(0);
    expect(container?.textContent).not.toContain("Autofill Preview");
    expect(container?.textContent).not.toContain("Fill Safe Fields");
  });

  it("dispatches exactly one complete FILL_FORM for one explicit click and shows actual execution counts", async () => {
    let resolveFill: ((value: unknown) => void) | undefined;
    const pending = new Promise<unknown>((resolve) => { resolveFill = resolve; });
    const sendMessage = await renderApp({ fill: () => pending });
    await clickButton("Review autofill");
    await clickButton("Fill Safe Fields");
    expect(Array.from(container?.querySelectorAll("button") ?? []).find((button) => button.textContent === "Filling…")?.hasAttribute("disabled")).toBe(true);
    await clickButton("Filling…");
    expect(sendMessage.mock.calls.filter(([message]) => message.type === "FILL_FORM")).toHaveLength(1);
    await act(async () => { resolveFill?.({ type: "AUTOFILL_COMPLETE", payload: { attempted: 3, filled: 2, failed: 1, skipped: 2 } }); await Promise.resolve(); await Promise.resolve(); });
    expect(container?.textContent).toContain("Actual execution result");
    expect(container?.textContent).toContain("3 attempted");
    expect(container?.textContent).toContain("2 filled");
    expect(container?.textContent).toContain("1 failed");
    expect(container?.textContent).toContain("2 skipped");
  });

  it("recovers to disconnected UI when fill reports revoked authorization", async () => {
    let sessionReads = 0;
    const sendMessage = vi.fn(async (message: { type: string }) => {
      if (message.type === "GET_SESSION_STATE") { sessionReads++; return { type: "SESSION_STATE", payload: sessionReads === 1 ? { connected: true, user: { id: 1, email: "owner@example.test", name: "Owner" } } : { connected: false } }; }
      if (message.type === "GET_TAB_CONTEXT") return { type: "TAB_CONTEXT", payload: { context: boundContext, fields: detectedFields } };
      if (message.type === "GET_AUTOFILL_PROFILE") return { type: "AUTOFILL_PROFILE", payload: profile };
      if (message.type === "FILL_FORM") throw new Error("Session expired");
      return { type: "TAB_CONTEXT", payload: { context: null } };
    });
    (globalThis as { chrome: unknown }).chrome = { runtime: { sendMessage, onMessage: { addListener: vi.fn(), removeListener: vi.fn() } }, tabs: { query: vi.fn() }, storage: { onChanged: { addListener: vi.fn(), removeListener: vi.fn() } } } as unknown as typeof chrome;
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    await act(async () => { root?.render(<App />); await Promise.resolve(); await Promise.resolve(); });
    await clickButton("Review autofill"); await clickButton("Fill Safe Fields");
    expect(sessionReads).toBe(2);
    expect(container?.textContent).not.toContain("Autofill Preview");
    expect(container?.textContent).not.toContain("Actual execution result");
    expect(container?.textContent).toContain("Connect");
  });
});

describe("autofill preview", () => {
  const profile = { personal: { first_name: "Owner", last_name: "Example", email: "owner@example.test", phone: null, city: null, country: null }, links: { linkedin: null, github: null, portfolio: null, website: null }, work: { current_title: null, current_company: null, years_experience: null, expected_salary: "100000" }, authorization: { requires_sponsorship: null, authorized_to_work: null }, education: [], experience: [], skills: [] };
  const fields = [
    { elementId: "first", tagName: "input", inputType: "text", label: "First Name", visible: true, selectorPath: "#first" },
    { elementId: "salary", tagName: "input", inputType: "text", label: "Expected Salary", visible: true, selectorPath: "#salary" },
    { elementId: "veteran", tagName: "select", inputType: "select", label: "Veteran Status", visible: true, selectorPath: "#veteran" },
  ];

  it("preserves complete deterministic mappings and only enables explicit safe fill", async () => {
    const onFill = vi.fn();
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    await act(async () => { root?.render(<AutofillPreviewPage profile={profile} fields={fields} filling={false} onFill={onFill} onBack={vi.fn()} />); });
    expect(container.textContent).toContain("1 safe"); expect(container.textContent).toContain("1 review");
    await act(async () => { Array.from(container?.querySelectorAll("button") ?? []).find((button) => button.textContent === "Fill Safe Fields")?.click(); });
    expect(onFill).toHaveBeenCalledTimes(1);
    expect(onFill.mock.calls[0][0]).toEqual(expect.arrayContaining([
      expect.objectContaining({ elementId: "first", selectorPath: "#first", action: "fill", profilePath: "personal.first_name" }),
      expect.objectContaining({ elementId: "salary", selectorPath: "#salary", action: "review" }),
      expect.objectContaining({ elementId: "veteran", selectorPath: "#veteran", action: "skip" }),
    ]));
  });

  it("keeps custom questions in the complete mapping set without making them safe", async () => {
    const onFill = vi.fn();
    const customFields = [
      { elementId: "first", tagName: "input", inputType: "text", label: "First Name", visible: true, selectorPath: "#first" },
      { elementId: "greenhouse-experience", tagName: "input", inputType: "text", label: "Do you have at least 3 years experience advising on talent-related functions?", visible: true, selectorPath: "#greenhouse-experience" },
      { elementId: "custom-email", tagName: "input", inputType: "text", label: "Please confirm your email address is still valid for interview scheduling", visible: true, selectorPath: "#custom-email" },
    ];
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    await act(async () => { root?.render(<AutofillPreviewPage profile={profile} fields={customFields} filling={false} onFill={onFill} onBack={vi.fn()} />); });
    expect(container.textContent).toContain("1 safe"); expect(container.textContent).toContain("2 skipped");
    await act(async () => { Array.from(container?.querySelectorAll("button") ?? []).find((button) => button.textContent === "Fill Safe Fields")?.click(); });
    expect(onFill.mock.calls[0][0]).toEqual(expect.arrayContaining([
      expect.objectContaining({ elementId: "greenhouse-experience", selectorPath: "#greenhouse-experience", action: "skip", profilePath: null }),
      expect.objectContaining({ elementId: "custom-email", selectorPath: "#custom-email", action: "skip", profilePath: null }),
    ]));
  });

  it("renders actual worker execution counts separately from preview counts", async () => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    await act(async () => { root?.render(<AutofillPreviewPage profile={profile} fields={fields} filling={false} execution={{ attempted: 1, filled: 0, failed: 1, skipped: 2 }} onFill={vi.fn()} onBack={vi.fn()} />); });
    expect(container.textContent).toContain("Actual execution result"); expect(container.textContent).toContain("1 attempted"); expect(container.textContent).toContain("1 failed"); expect(container.textContent).toContain("2 skipped");
  });
});
