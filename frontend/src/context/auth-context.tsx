"use client";

/**
 * Authentication session provider.
 *
 * The access token lives only in memory (see lib/api.ts). On a full page
 * reload that in-memory token is gone, so this provider silently calls
 * `/auth/refresh` on mount — the browser sends the httpOnly refresh cookie
 * and we get a fresh access token + user, restoring the session without a
 * visible re-login.
 *
 * It also registers the global "auth expired" handler so that when a refresh
 * ultimately fails (cookie missing/expired/revoked) the app redirects to the
 * sign-in page exactly once.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  authApi,
  setAccessToken,
  setAuthExpiredHandler,
} from "@/lib/api";
import type { AuthUser, TokenResponse } from "@/lib/types";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, name: string, password: string) => Promise<void>;
  loginWithGoogle: (idToken: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const bootstrapped = useRef(false);

  const applySession = useCallback((res: TokenResponse) => {
    setAccessToken(res.access_token);
    setUser(res.user);
    setStatus("authenticated");
  }, []);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    setStatus("unauthenticated");
    queryClient.clear();
  }, [queryClient]);

  // Silent re-auth on mount (and global expiry handler). Runs once.
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;

    setAuthExpiredHandler(() => {
      clearSession();
      router.replace("/signin");
    });

    let cancelled = false;
    (async () => {
      try {
        const res = await authApi.refresh();
        if (!cancelled) applySession(res);
      } catch {
        if (!cancelled) {
          setAccessToken(null);
          setUser(null);
          setStatus("unauthenticated");
        }
      }
    })();

    return () => {
      cancelled = true;
      setAuthExpiredHandler(null);
    };
  }, [applySession, clearSession, router]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await authApi.login({ email, password });
      applySession(res);
    },
    [applySession],
  );

  const register = useCallback(
    async (email: string, name: string, password: string) => {
      const res = await authApi.register({ email, name, password });
      applySession(res);
    },
    [applySession],
  );

  const loginWithGoogle = useCallback(
    async (idToken: string) => {
      const res = await authApi.google(idToken);
      applySession(res);
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      clearSession();
      router.replace("/signin");
    }
  }, [clearSession, router]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, register, loginWithGoogle, logout }),
    [user, status, login, register, loginWithGoogle, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
