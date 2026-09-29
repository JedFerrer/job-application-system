import { StrictMode, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/context/auth-context";
import { AuthGuard } from "@/components/auth/auth-guard";
import SignInPage from "@/app/signin/page";
import ExtensionConnectPage from "@/app/extension/connect/page";

const apiMocks = vi.hoisted(() => ({
  refresh: vi.fn(),
  setAccessToken: vi.fn(),
  setRefreshFailureHandler: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  google: vi.fn(),
  logout: vi.fn(),
}));
const routerMocks = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  authApi: {
    login: apiMocks.login,
    register: apiMocks.register,
    google: apiMocks.google,
    logout: apiMocks.logout,
  },
  refreshAccessToken: apiMocks.refresh,
  isExpiredRefresh: (error: unknown) =>
    typeof error === "object" && error !== null && "status" in error && error.status === 401,
  setAccessToken: apiMocks.setAccessToken,
  setRefreshFailureHandler: apiMocks.setRefreshFailureHandler,
}));
vi.mock("next/navigation", () => ({ useRouter: () => routerMocks }));
vi.mock("@/components/auth/google-sign-in-button", () => ({
  GoogleSignInButton: ({ onCredential }: { onCredential: (token: string) => Promise<void> }) => (
    <button onClick={() => void onCredential("test-google-token")}>Mock Google sign-in</button>
  ),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function Status() {
  const { status } = useAuth();
  return <output data-testid="auth-status">{status}</output>;
}

describe("auth session startup", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient();
    vi.clearAllMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
  });

  function render(children: React.ReactNode) {
    root.render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>{children}</AuthProvider>
        </QueryClientProvider>
      </StrictMode>,
    );
  }

  it("renders guarded content after a successful refresh during effect replay", async () => {
    const refresh = deferred<{ access_token: string; user: { id: number } }>();
    apiMocks.refresh.mockReturnValue(refresh.promise);

    await act(async () => {
      render(<><Status /><AuthGuard><p>Dashboard ready</p></AuthGuard></>);
    });
    expect(container.querySelector("output")?.textContent).toBe("loading");
    expect(apiMocks.refresh).toHaveBeenCalledTimes(2);

    await act(async () => {
      refresh.resolve({ access_token: "access-token", user: { id: 1 } });
      await refresh.promise;
    });

    expect(container.querySelector("output")?.textContent).toBe("authenticated");
    expect(container.textContent).toContain("Dashboard ready");
    expect(routerMocks.replace).not.toHaveBeenCalled();
  });

  it("redirects guarded content after a rejected refresh during effect replay", async () => {
    const refresh = deferred<never>();
    apiMocks.refresh.mockReturnValue(refresh.promise);

    await act(async () => {
      render(<><Status /><AuthGuard><p>Private content</p></AuthGuard></>);
    });

    await act(async () => {
      refresh.reject(Object.assign(new Error("No refresh cookie"), { status: 401 }));
      await refresh.promise.catch(() => undefined);
    });

    expect(container.querySelector("output")?.textContent).toBe("unauthenticated");
    expect(container.textContent).not.toContain("Private content");
    expect(routerMocks.replace).toHaveBeenCalledWith("/signin");
  });

  it("shows the sign-in form only after authentication fails", async () => {
    const refresh = deferred<never>();
    apiMocks.refresh.mockReturnValue(refresh.promise);

    await act(async () => { render(<SignInPage />); });
    expect(container.querySelector("form")).toBeNull();

    await act(async () => {
      refresh.reject(Object.assign(new Error("No refresh cookie"), { status: 401 }));
      await refresh.promise.catch(() => undefined);
    });

    expect(container.querySelector("form")).not.toBeNull();
    expect(routerMocks.replace).not.toHaveBeenCalled();
  });

  it("redirects an authenticated sign-in visitor without showing the form", async () => {
    const refresh = deferred<{ access_token: string; user: { id: number } }>();
    apiMocks.refresh.mockReturnValue(refresh.promise);

    await act(async () => { render(<SignInPage />); });
    expect(container.querySelector("form")).toBeNull();

    await act(async () => {
      refresh.resolve({ access_token: "access-token", user: { id: 1 } });
      await refresh.promise;
    });

    expect(container.querySelector("form")).toBeNull();
    expect(routerMocks.replace).toHaveBeenCalledWith("/dashboard");
  });

  it("redirects after Google sign-in updates the session", async () => {
    const refresh = deferred<never>();
    apiMocks.refresh.mockReturnValue(refresh.promise);
    apiMocks.google.mockResolvedValue({ access_token: "google-access", user: { id: 1 } });

    await act(async () => { render(<SignInPage />); });
    await act(async () => {
      refresh.reject(Object.assign(new Error("No refresh cookie"), { status: 401 }));
      await refresh.promise.catch(() => undefined);
    });

    await act(async () => {
      const button = Array.from(container.querySelectorAll("button")).find(
        (item) => item.textContent === "Mock Google sign-in",
      );
      button?.click();
    });

    expect(apiMocks.google).toHaveBeenCalledWith("test-google-token");
    expect(container.querySelector("form")).toBeNull();
    expect(routerMocks.replace).toHaveBeenCalledWith("/dashboard");
  });

  it("keeps guarded content hidden after a temporary failure and recovers on manual retry", async () => {
    const first = deferred<never>();
    queryClient.setQueryData(["private-profile"], { name: "Private value" });
    apiMocks.refresh.mockReturnValueOnce(first.promise).mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({ access_token: "restored", user: { id: 1 } });

    await act(async () => {
      render(<><Status /><AuthGuard><p>Private content</p></AuthGuard></>);
    });
    await act(async () => {
      first.reject(Object.assign(new Error("Backend unavailable"), { status: 502 }));
      await first.promise.catch(() => undefined);
    });

    expect(container.querySelector("output")?.textContent).toBe("unavailable");
    expect(container.textContent).toContain("Could not verify your session");
    expect(container.textContent).not.toContain("Private content");
    expect(routerMocks.replace).not.toHaveBeenCalled();
    expect(apiMocks.refresh).toHaveBeenCalledTimes(2);
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);

    await act(async () => {
      Array.from(container.querySelectorAll("button")).find(
        (item) => item.textContent === "Try again",
      )?.click();
    });
    expect(apiMocks.refresh).toHaveBeenCalledTimes(3);
    expect(container.querySelector("output")?.textContent).toBe("authenticated");
    expect(container.textContent).toContain("Private content");
  });

  it("shows sign-in after a manual retry receives 401", async () => {
    const failure = Object.assign(new Error("Backend unavailable"), { status: 502 });
    apiMocks.refresh.mockRejectedValueOnce(failure).mockRejectedValueOnce(failure)
      .mockRejectedValueOnce(Object.assign(new Error("Expired"), { status: 401 }));

    await act(async () => { render(<><Status /><AuthGuard><p>Private content</p></AuthGuard></>); });
    expect(container.querySelector("output")?.textContent).toBe("unavailable");
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find(
        (item) => item.textContent === "Try again",
      )?.click();
    });
    expect(container.querySelector("output")?.textContent).toBe("unauthenticated");
    expect(routerMocks.replace).toHaveBeenCalledWith("/signin");
  });

  it("shows recovery instead of the sign-in form when refresh is unavailable", async () => {
    apiMocks.refresh.mockRejectedValue(Object.assign(new Error("Network failure"), { status: 502 }));
    await act(async () => { render(<SignInPage />); });
    expect(container.textContent).toContain("Could not verify your session");
    expect(container.querySelector("form")).toBeNull();
    expect(routerMocks.replace).not.toHaveBeenCalled();
  });

  it("hides extension pairing while the session cannot be verified", async () => {
    apiMocks.refresh.mockRejectedValue(new Error("Network failure"));
    await act(async () => { render(<ExtensionConnectPage />); });
    expect(container.textContent).toContain("Could not verify your session");
    expect(container.textContent).not.toContain("Generate Pairing Code");
    expect(routerMocks.replace).not.toHaveBeenCalled();
  });
});
