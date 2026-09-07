import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SourceRunSummary } from "@/components/jobs/source-run-summary";

describe("SourceRunSummary", () => {
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

  it("labels an uncertain empty source without inventing a failure diagnosis", async () => {
    await act(async () => {
      root.render(
        <SourceRunSummary results={[{
          source: "indeed", status: "empty", fetched: 0, accepted: 0, rejected: 0,
          truncated: false, completeness: "unknown",
        }]} />,
      );
    });

    expect(container.textContent).toContain("No results returned");
    expect(container.textContent).toContain("completeness unknown");
  });
});
