import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { PreparationChecklist } from "@/components/applications/preparation-checklist";

describe("PreparationChecklist", () => {
  it("treats an omitted letter and absent questions as satisfied without inflating the denominator", async () => {
    const node = document.createElement("div");
    const root = createRoot(node);
    await act(async () => root.render(<PreparationChecklist preparation={{ application_id: 1, version: 1, state: "in_progress", fit_acknowledged: true, fit_context_available: false, resume_draft_id: 4, resume_current: true, cover_letter_mode: "not_needed", cover_letter_id: null, cover_letter_current: false, manual_questions: [], approved_package_id: null, approval_current: false }} />));
    expect(node.textContent).toContain("3 of 3 complete");
    expect(node.textContent).toContain("Match analysis is unavailable");
    root.unmount();
  });

  it("shows the optional manual component only when server questions exist", async () => {
    const node = document.createElement("div");
    const root = createRoot(node);
    const base = { application_id: 1, version: 1, state: "in_progress" as const, fit_acknowledged: true, fit_context_available: true, resume_draft_id: 4, resume_current: true, cover_letter_mode: "not_needed" as const, cover_letter_id: null, cover_letter_current: false, approved_package_id: null, approval_current: false };
    await act(async () => root.render(<PreparationChecklist preparation={{ ...base, manual_questions: [{ id: "00000000-0000-0000-0000-000000000001", question: "Portfolio?", answer: "", acknowledged: false }] }} />));
    expect(node.textContent).toContain("3 of 4 complete");
    expect(node.textContent).toContain("0 of 1 complete");
    await act(async () => root.render(<PreparationChecklist preparation={{ ...base, manual_questions: [{ id: "00000000-0000-0000-0000-000000000001", question: "Portfolio?", answer: "", acknowledged: true }] }} />));
    expect(node.textContent).toContain("4 of 4 complete");
    expect(node.textContent).toContain("1 of 1 complete");
    root.unmount();
  });
});
