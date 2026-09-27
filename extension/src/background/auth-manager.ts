// ── Extension Auth Manager ─────────────────────────────────────────────────
// Handles pairing code exchange, token storage, session state, and logout.

import { apiRequest, storeTokens, clearExtensionSession, loadTokens } from "./api-client";
import { STORAGE_KEYS } from "../shared/constants";

export interface SessionState {
  connected: boolean;
  user?: { id: number; email: string; name: string };
}

/** Exchange a pairing code for tokens. */
export async function completePairing(code: string): Promise<SessionState> {
  const data = await apiRequest<{
    access_token: string;
    refresh_token: string;
    expires_in: number;
    user: { id: number; email: string; name: string };
    session_id: string;
  }>("/extension/auth/complete", {
    method: "POST",
    body: JSON.stringify({ pairing_code: code }),
    skipAuth: true,
  });

  await storeTokens(data.access_token, data.refresh_token, data.expires_in);
  await chrome.storage.session.set({
    [STORAGE_KEYS.USER]: data.user,
    [STORAGE_KEYS.SESSION_ID]: data.session_id,
  });

  return { connected: true, user: data.user };
}

/** Get current session state. */
export async function getSessionState(): Promise<SessionState> {
  const authed = await loadTokens();
  if (!authed) return { connected: false };

  const result = await chrome.storage.session.get(STORAGE_KEYS.USER);
  const user = result[STORAGE_KEYS.USER] as { id: number; email: string; name: string } | undefined;

  return {
    connected: true,
    user: user ?? { id: 0, email: "unknown", name: "Unknown" },
  };
}

/** Logout and revoke the extension session. */
export async function logout(): Promise<void> {
  try {
    // The backend derives the session from the validated access-token claim.
    await apiRequest("/extension/auth/logout", { method: "POST" });
  } catch {
    // Best-effort: revoke may fail if token is already expired
  } finally {
    await clearExtensionSession();
  }
}
