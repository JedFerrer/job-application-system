// ── Extension API Client ───────────────────────────────────────────────────
// Calls the FastAPI backend directly (extension has its own origin, not
// through the Next.js proxy). Manages access/refresh tokens in chrome.storage.session.

import { API_BASE_URL, STORAGE_KEYS } from "../shared/constants";
import { clearAllTabContexts } from "./tab-context";

let accessToken: string | null = null;
let refreshPromise: Promise<boolean> | null = null;

/** Persist tokens to chrome.storage.session (cleared when browser closes). */
export async function storeTokens(access: string, refresh: string, expiresIn: number): Promise<void> {
  accessToken = access;
  await chrome.storage.session.set({
    [STORAGE_KEYS.ACCESS_TOKEN]: access,
    [STORAGE_KEYS.REFRESH_TOKEN]: refresh,
    [STORAGE_KEYS.TOKEN_EXPIRES_AT]: Date.now() + expiresIn * 1000,
  });
}

/** Load tokens from storage on service worker startup. */
export async function loadTokens(): Promise<boolean> {
  const result = await chrome.storage.session.get([
    STORAGE_KEYS.ACCESS_TOKEN,
    STORAGE_KEYS.REFRESH_TOKEN,
    STORAGE_KEYS.TOKEN_EXPIRES_AT,
  ]);
  const token = result[STORAGE_KEYS.ACCESS_TOKEN] as string | undefined;
  const expiresAt = result[STORAGE_KEYS.TOKEN_EXPIRES_AT] as number | undefined;

  if (token && expiresAt && Date.now() < expiresAt) {
    accessToken = token;
    return true;
  }

  // Token expired or missing — try refresh
  const refreshToken = result[STORAGE_KEYS.REFRESH_TOKEN] as string | undefined;
  if (refreshToken) {
    return tryRefreshToken(refreshToken);
  }
  return false;
}

/** Clear all tokens from memory and storage. */
export async function clearTokens(): Promise<void> {
  accessToken = null;
  await chrome.storage.session.remove([
    STORAGE_KEYS.ACCESS_TOKEN,
    STORAGE_KEYS.REFRESH_TOKEN,
    STORAGE_KEYS.TOKEN_EXPIRES_AT,
    STORAGE_KEYS.USER,
    STORAGE_KEYS.SESSION_ID,
  ]);
}

/** Clear credentials and every correlation artifact after session invalidation. */
export async function clearExtensionSession(): Promise<void> {
  await clearTokens();
  await clearAllTabContexts();
}

async function tryRefreshToken(refreshToken: string): Promise<boolean> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/extension/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        await clearExtensionSession();
        return false;
      }
      const data = await res.json();
      await storeTokens(data.access_token, data.refresh_token, data.expires_in);
      return true;
    } catch {
      await clearExtensionSession();
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

/**
 * Make an authenticated API request to the backend.
 * Automatically refreshes the token on 401.
 */
export async function apiRequest<T>(
  path: string,
  options: RequestInit & { skipAuth?: boolean; _retried?: boolean } = {},
): Promise<T> {
  const { skipAuth = false, _retried = false, ...init } = options;
  const headers: Record<string, string> = {};

  if (!(init.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  if (!skipAuth && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  if (init.headers) {
    Object.assign(headers, init.headers);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });

  // Transparent single-shot refresh on 401
  if (response.status === 401 && !skipAuth && !_retried) {
    const result = await chrome.storage.session.get(STORAGE_KEYS.REFRESH_TOKEN);
    const refresh = result[STORAGE_KEYS.REFRESH_TOKEN] as string | undefined;
    if (refresh) {
      const ok = await tryRefreshToken(refresh);
      if (ok) {
        return apiRequest<T>(path, { ...options, _retried: true });
      }
    }
    await clearExtensionSession();
    throw new ApiError("Authentication expired", 401);
  }

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = `HTTP ${response.status}`;
    }
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      response.status,
    );
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Check if the user is currently authenticated. */
export function isAuthenticated(): boolean {
  return accessToken !== null;
}

/** Get the current access token (for direct use). */
export function getToken(): string | null {
  return accessToken;
}
