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
          source: "indeed", status: "empty", fetched: 0, inserted: 0, updated: 0,
          embedded: 0, accepted: 0, rejected: 0,
          truncated: false, completeness: "unknown",
        }]} />,
      );
    });

    expect(container.textContent).toContain("No results returned");
    expect(container.textContent).toContain("completeness unknown");
  });

  it("shows both executed and already-fresh sources", async () => {
    await act(async () => {
      root.render(
        <SourceRunSummary results={[
          {
            source: "indeed", status: "success", fetched: 3, inserted: 2, updated: 1,
            embedded: 0, accepted: 3, rejected: 0, truncated: false, completeness: "known_complete",
          },
          {
            source: "linkedin", status: "skipped_fresh", fetched: 0, inserted: 0, updated: 0,
            embedded: 0, accepted: 0, rejected: 0, truncated: false, completeness: "known_complete",
          },
        ]} />,
      );
    });

    expect(container.textContent).toContain("indeed");
    expect(container.textContent).toContain("linkedin");
    expect(container.textContent).toContain("Already fresh");
  });
});
