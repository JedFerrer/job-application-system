import { StrictMode, act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/context/auth-context";
import { AuthGuard } from "@/components/auth/auth-guard";
import { getAccessToken, resumeApi, setAccessToken } from "@/lib/api";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

function ProfileRequest() {
  const { status } = useAuth();
  return (
    <>
      <output>{status}</output>
      <AuthGuard>
        <button onClick={() => void resumeApi.getProfile().catch(() => {})}>
          Load profile
        </button>
        <p>Private content</p>
      </AuthGuard>
    </>
  );
}

describe("auth recovery across the API client and route guard", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    setAccessToken(null);
  });

  it("hides private data after a refresh 502 and restores it only after a manual retry", async () => {
    let refreshCount = 0;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/backend/auth/refresh") {
        refreshCount += 1;
        if (refreshCount === 2) {
          return Promise.resolve(new Response(JSON.stringify({ detail: "Backend unavailable" }), {
            status: 502,
            headers: { "Content-Type": "application/json" },
          }));
        }
        return Promise.resolve(Response.json({
          access_token: `access-${refreshCount}`,
          user: { id: 1, email: "qa@example.com" },
        }));
      }
      return Promise.resolve(new Response(JSON.stringify({ detail: "Expired access token" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const queryClient = new QueryClient();
    try {
      await act(async () => {
        root.render(
          <StrictMode>
            <QueryClientProvider client={queryClient}>
              <AuthProvider><ProfileRequest /></AuthProvider>
            </QueryClientProvider>
          </StrictMode>,
        );
      });
      expect(container.querySelector("output")?.textContent).toBe("authenticated");
      expect(container.textContent).toContain("Private content");
      expect(refreshCount).toBe(1);

      queryClient.setQueryData(["private-profile"], { name: "Secret" });
      await act(async () => {
        Array.from(container.querySelectorAll("button")).find(
          (button) => button.textContent === "Load profile",
        )?.click();
      });
      expect(container.querySelector("output")?.textContent).toBe("unavailable");
      expect(container.textContent).toContain("Could not verify your session");
      expect(container.textContent).not.toContain("Private content");
      expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
      expect(getAccessToken()).toBeNull();
      expect(refreshCount).toBe(2);
      expect(router.replace).not.toHaveBeenCalled();

      await act(async () => {
        Array.from(container.querySelectorAll("button")).find(
          (button) => button.textContent === "Try again",
        )?.click();
      });
      expect(refreshCount).toBe(3);
      expect(container.querySelector("output")?.textContent).toBe("authenticated");
      expect(container.textContent).toContain("Private content");
      expect(getAccessToken()).toBe("access-3");
    } finally {
      act(() => root.unmount());
      queryClient.clear();
      container.remove();
    }
  });
});
