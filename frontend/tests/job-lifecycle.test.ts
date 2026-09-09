import { describe, expect, it } from "vitest";
import { getJobLifecyclePresentation } from "@/lib/job-lifecycle";

describe("job lifecycle presentation", () => {
  it("keeps observation freshness distinct from the search-age lifecycle", () => {
    const lifecycle = getJobLifecyclePresentation({
      status: "expired",
      first_seen_at: "2026-08-01T12:00:00Z",
      last_seen_at: "2026-08-10T12:00:00Z",
      not_recently_seen: true,
    });

    expect(lifecycle.statusLabel).toBe("Outside search age window");
    expect(lifecycle.isReadOnly).toBe(true);
    expect(lifecycle.firstFound).not.toBe("Unknown");
    expect(lifecycle.lastObserved).not.toBe("Unknown");
  });

  it("renders missing observation timestamps honestly", () => {
    const lifecycle = getJobLifecyclePresentation({ status: "unknown" });

    expect(lifecycle.firstFound).toBe("Unknown");
    expect(lifecycle.lastObserved).toBe("Unknown");
    expect(lifecycle.statusLabel).toBe("Lifecycle unknown");
  });

  it("does not describe fresh or stale observations as closed", () => {
    expect(
      getJobLifecyclePresentation({ status: "active", not_recently_seen: true }).statusLabel,
    ).toBe("Not recently seen");
    expect(getJobLifecyclePresentation({ status: "active" }).statusLabel).toBeNull();
    expect(getJobLifecyclePresentation({ status: "closed" }).statusLabel).toBe(
      "Explicitly closed",
    );
  });
});
