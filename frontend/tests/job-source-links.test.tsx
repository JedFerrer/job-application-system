import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import { JobSourceLinks } from "@/components/jobs/job-source-links";
import type { JobSourceLink } from "@/lib/types";

const stamp = {
  first_seen_at: "2026-01-01T00:00:00Z",
  last_seen_at: "2026-01-01T00:00:00Z",
  status: "active",
};

const links: JobSourceLink[] = [
  { source: "indeed", source_url: "https://example.test/one", canonical_url: "https://example.test/one", external_apply_url: null, ...stamp },
  { source: "greenhouse", source_url: "https://example.test/two", canonical_url: "https://example.test/two", external_apply_url: "https://careers.example.test/two", ...stamp },
];

const sharedApply: JobSourceLink[] = [
  {
    source: "qa-unit-1a-success",
    source_url: "https://qa.invalid/unit-1a/mixed-success",
    canonical_url: "https://qa.invalid/unit-1a/mixed-success",
    external_apply_url: "https://boards.greenhouse.io/qa-fixture/jobs/424242",
    ...stamp,
  },
  {
    source: "lever",
    source_url: "https://jobs.lever.co/qa-fixture/11111111-1111-1111-1111-111111111111",
    canonical_url: "https://jobs.lever.co/qa-fixture/11111111-1111-1111-1111-111111111111",
    external_apply_url: "https://boards.greenhouse.io/qa-fixture/jobs/424242",
    ...stamp,
  },
];

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

it("renders safe alternate links only when more than one alias exists", async () => {
  await act(async () => root.render(<JobSourceLinks links={links} />));
  expect(container.querySelector("section")?.getAttribute("aria-label")).toBe("Alternate job links");
  const anchors = [...container.querySelectorAll("a")];
  expect(anchors.map((anchor) => [anchor.textContent?.trim(), anchor.getAttribute("href")])).toEqual([
    ["Open via indeed", "https://example.test/one"],
    ["Open via greenhouse", "https://example.test/two"],
    ["Open employer application", "https://careers.example.test/two"],
  ]);
});

it("does not render a section for a single source alias", async () => {
  const [single] = links;
  if (single === undefined) {
    throw new Error("expected a single-source fixture");
  }
  await act(async () => root.render(<JobSourceLinks links={[single]} />));
  expect(container.querySelector("section")).toBeNull();
});

it("opens each source alias at its own listing URL and labels the shared apply URL separately", async () => {
  await act(async () => root.render(<JobSourceLinks links={sharedApply} />));
  const anchors = [...container.querySelectorAll("a")];
  expect(anchors).toHaveLength(3);
  expect(anchors[0]?.textContent).toContain("Open via qa-unit-1a-success");
  expect(anchors[0]?.getAttribute("href")).toBe("https://qa.invalid/unit-1a/mixed-success");
  expect(anchors[1]?.textContent).toContain("Open via lever");
  expect(anchors[1]?.getAttribute("href")).toBe(
    "https://jobs.lever.co/qa-fixture/11111111-1111-1111-1111-111111111111",
  );
  expect(anchors[2]?.textContent).toContain("Open employer application");
  expect(anchors[2]?.getAttribute("href")).toBe("https://boards.greenhouse.io/qa-fixture/jobs/424242");
  expect(container.textContent).not.toMatch(/Open via (lever|qa-unit-1a-success)[\s\S]*greenhouse/i);
  expect(
    anchors.filter((anchor) => anchor.getAttribute("href") === "https://boards.greenhouse.io/qa-fixture/jobs/424242"),
  ).toHaveLength(1);
});
