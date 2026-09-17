import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConnectedAppsPage from "@/app/dashboard/settings/connected-apps/page";

const { listSessions, revokeSession } = vi.hoisted(() => ({
  listSessions: vi.fn(),
  revokeSession: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  extensionApi: { listSessions, revokeSession },
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { sessions: [{
    id: "revoked-session",
    device_name: null,
    browser: "Chrome",
    created_at: "2026-09-12T00:00:00Z",
    last_used_at: null,
    revoked: true,
  }] }.sessions, isLoading: false, error: null }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

describe("ConnectedAppsPage", () => {
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

  it("offers a pairing action and recovery guidance when the only session is revoked", async () => {
    await act(async () => {
      root.render(<ConnectedAppsPage />);
    });

    const pairingLink = Array.from(container.querySelectorAll("a")).find(
      (link) => link.textContent === "Pair Chrome extension",
    );

    expect(pairingLink?.getAttribute("href")).toBe("/extension/connect");
    expect(container.textContent).toContain(
      "This session was revoked. Pair the extension again to reconnect it.",
    );
  });
});
