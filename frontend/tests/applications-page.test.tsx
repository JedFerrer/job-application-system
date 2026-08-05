import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import ApplicationsPage from "@/app/dashboard/applications/page";

vi.mock("@/components/applications/applications-panel", () => ({
  ApplicationsPanel: () => null,
}));

describe("ApplicationsPage workspace navigation", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("shows sibling workspace links and marks Applications active", async () => {
    await act(async () => {
      root.render(<ApplicationsPage />);
    });

    expect(container.querySelector('a[href="/applications"]')?.textContent).toContain("Applications");
    expect(container.querySelector('a[href="/resume"]')?.textContent).toContain("Tailored resumes");
    expect(container.querySelector('a[href="/applications"]')?.getAttribute("aria-current")).toBe("page");
    expect(container.querySelector('a[href="/resume"]')?.getAttribute("aria-current")).toBeNull();
    expect(container.querySelector('a[href="/settings/profile"]')).toBeNull();
  });
});
