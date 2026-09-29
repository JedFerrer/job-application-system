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
 * A 401 from refresh ends the session. Transport and server failures leave the
 * cookie in place and require a deliberate retry before showing private data.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  authApi,
  isExpiredRefresh,
  refreshAccessToken,
  setAccessToken,
  setRefreshFailureHandler,
} from "@/lib/api";
import type { AuthUser, TokenResponse } from "@/lib/types";

type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "unavailable";

interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, name: string, password: string) => Promise<void>;
  loginWithGoogle: (idToken: string) => Promise<void>;
  logout: () => Promise<void>;
  retrySession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

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

  const markUnavailable = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    queryClient.clear();
    setStatus("unavailable");
  }, [queryClient]);

  const retrySession = useCallback(async () => {
    setStatus("loading");
    try {
      applySession(await refreshAccessToken());
    } catch (error) {
      if (isExpiredRefresh(error)) clearSession();
      else markUnavailable();
    }
  }, [applySession, clearSession, markUnavailable]);

  // Silent re-auth on mount (and global expiry handler). Each effect setup must
  // handle the result, including React's development cleanup/setup replay.
  useEffect(() => {
    setRefreshFailureHandler((failure) => {
      if (failure === "expired") {
        clearSession();
        router.replace("/signin");
      } else {
        markUnavailable();
      }
    });

    let cancelled = false;
    (async () => {
      try {
        const res = await refreshAccessToken();
        if (!cancelled) applySession(res);
      } catch (error) {
        if (!cancelled) {
          if (isExpiredRefresh(error)) clearSession();
          else markUnavailable();
        }
      }
    })();

    return () => {
      cancelled = true;
      setRefreshFailureHandler(null);
    };
  }, [applySession, clearSession, markUnavailable, router]);

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
    () => ({ user, status, login, register, loginWithGoogle, logout, retrySession }),
    [user, status, login, register, loginWithGoogle, logout, retrySession],
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
