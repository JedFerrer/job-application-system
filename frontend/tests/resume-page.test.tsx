import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import ResumePage from "@/app/dashboard/resume/page";

vi.mock("@/components/resume/resume-panel", () => ({
  ResumePanel: () => null,
}));

describe("ResumePage", () => {
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

  it("labels the page as tailored resumes and links to profile management", async () => {
    await act(async () => {
      root.render(<ResumePage />);
    });

    expect(container.textContent).toContain("Tailored resumes");
    expect(container.querySelector('a[href="/settings/profile"]')).toBeNull();
    expect(container.querySelector('a[href="/applications"]')?.textContent).toContain("Applications");
    expect(container.querySelector('a[href="/resume"]')?.getAttribute("aria-current")).toBe("page");
    expect(container.querySelector('a[href="/applications"]')?.getAttribute("aria-current")).toBeNull();
  });
});
