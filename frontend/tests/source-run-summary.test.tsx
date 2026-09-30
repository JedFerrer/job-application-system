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
    expect(container.textContent).toContain("Source completeness unknown.");
    expect(container.textContent).toContain("Returned0Accepted0");
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

    expect(container.textContent).toContain("Indeed");
    expect(container.textContent).toContain("LinkedIn");
    expect(container.textContent).toContain("Already fresh");
    expect(container.textContent).toContain("2 new · 1 updated");
    expect(container.querySelectorAll("li")[1]?.textContent).not.toContain("Returned");
    expect(container.querySelector('[role="status"]')?.textContent).toContain("LinkedIn: Already fresh");
  });

  it("keeps failed sources distinct from successful counts and flags limited results", async () => {
    await act(async () => {
      root.render(
        <SourceRunSummary results={[
          {
            source: "zip_recruiter", status: "success", fetched: 5, inserted: 1, updated: 3,
            embedded: 0, accepted: 4, rejected: 1, truncated: true,
            completeness: "unknown",
          },
          {
            source: "glassdoor", status: "failed", fetched: 0, inserted: 0, updated: 0,
            embedded: 0, accepted: 0, rejected: 0, truncated: false,
            completeness: "unknown",
          },
        ]} />,
      );
    });

    expect(container.textContent).toContain("ZipRecruiter");
    expect(container.querySelectorAll("li")[0]?.textContent).toContain("1 rejected");
    expect(container.querySelectorAll("li")[0]?.textContent).toContain("Results may be incomplete.");
    expect(container.querySelectorAll("li")[1]?.textContent).toContain("Failed");
    expect(container.querySelectorAll("li")[1]?.textContent).not.toContain("Accepted0");
  });

  it.each([
    ["queued", "Queued"],
    ["running", "Running"],
    ["unsupported", "Unsupported"],
    ["unavailable", "Unavailable"],
  ] as const)("shows %s without implying any jobs were accepted", async (status, label) => {
    await act(async () => {
      root.render(<SourceRunSummary results={[{
        source: "indeed", status, fetched: 0, inserted: 0, updated: 0,
        embedded: 0, accepted: 0, rejected: 0, truncated: false,
        completeness: "unknown",
      }]} />);
    });

    expect(container.querySelector("li")?.textContent).toContain(label);
    expect(container.querySelector("li")?.textContent).not.toContain("Accepted");
  });

  it("hides the current results and shows a new mounted refresh", async () => {
    const results = [{
      source: "indeed", status: "success" as const, fetched: 2, inserted: 1, updated: 1,
      embedded: 0, accepted: 2, rejected: 0, truncated: false,
      completeness: "known_complete" as const,
    }];

    await act(async () => root.render(<SourceRunSummary results={results} />));
    await act(async () => {
      (container.querySelector('[aria-label="Hide refresh results"]') as HTMLButtonElement).click();
    });
    expect(container.querySelector("section")).toBeNull();

    await act(async () => root.render(null));
    await act(async () => root.render(<SourceRunSummary results={results} />));
    expect(container.textContent).toContain("Refresh results");
  });
});
