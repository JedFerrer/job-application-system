import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ingest = vi.fn();
const setTask = vi.fn();

vi.mock("@/lib/api", () => ({
  jobsApi: { ingest, listSourceCapabilities: vi.fn().mockResolvedValue([]) },
}));
vi.mock("@/context/jobs-task-context", () => ({
  useJobsTask: () => ({ isRunning: false, progressMsg: null, setTask }),
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ isSuccess: true, data: [] }),
}));

describe("Unit1AQaControls", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetModules();
    ingest.mockReset().mockResolvedValue({ task_id: "qa-task" });
    setTask.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("uses the normal ingest endpoint with deterministic mixed sources in a QA build", async () => {
    vi.stubEnv("NEXT_PUBLIC_UNIT_1A_BROWSER_QA", "true");
    const { Unit1AQaControls } = await import("@/components/jobs/unit-1a-qa-controls");
    await act(async () => root.render(<Unit1AQaControls />));

    const button = Array.from(container.querySelectorAll("button")).find((item) => item.textContent === "Run mixed outcomes");
    await act(async () => button?.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    expect(ingest).toHaveBeenCalledWith(expect.objectContaining({
      country: "QA",
      sources: ["qa-unit-1a-success", "qa-unit-1a-failure", "qa-unit-1a-uncertain-empty"],
    }));
    expect(setTask).toHaveBeenCalledWith("qa-task", expect.any(Array));
  });
});
