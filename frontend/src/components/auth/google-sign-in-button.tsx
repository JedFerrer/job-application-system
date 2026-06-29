"use client";

/**
 * Google Identity Services (GSI) sign-in button.
 *
 * Loads the GSI script once, renders Google's button, and forwards the
 * returned ID token to the parent. Requires NEXT_PUBLIC_GOOGLE_CLIENT_ID.
 * If the env var is absent the component renders nothing (graceful scaffold
 * until credentials are provisioned).
 */

import { useEffect, useRef } from "react";

const GSI_SRC = "https://accounts.google.com/gsi/client";

// Module-level guards: GSI must only be initialized once per page load,
// and the script must only be loaded once. React StrictMode mounts
// components twice in development, which would otherwise race.
let _gsiInitialized = false;
let _gsiLoadPromise: Promise<void> | null = null;

interface GoogleCredentialResponse {
  credential?: string;
}

interface GoogleAccountsId {
  initialize: (config: {
    client_id: string;
    callback: (response: GoogleCredentialResponse) => void;
  }) => void;
  renderButton: (
    parent: HTMLElement,
    options: Record<string, unknown>,
  ) => void;
}

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleAccountsId } };
  }
}

/**
 * Load the GSI client script. Caches the promise at module level so
 * multiple effect invocations (e.g. StrictMode double-mount) share the
 * same load.  When the script tag is already in the DOM (from a previous
 * mount that was cleaned up), polls until google.accounts.id is ready.
 */
function loadGsiScript(): Promise<void> {
  if (_gsiLoadPromise) return _gsiLoadPromise;

  _gsiLoadPromise = new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${GSI_SRC}"]`)) {
      // Script tag exists but google.accounts.id may not be ready yet
      // (StrictMode double-mount race). Poll until available.
      const maxAttempts = 50; // 5 s
      let attempts = 0;
      const poll = () => {
        if (window.google?.accounts?.id) {
          resolve();
        } else if (++attempts < maxAttempts) {
          setTimeout(poll, 100);
        } else {
          _gsiLoadPromise = null;
          reject(new Error("Google sign-in timed out"));
        }
      };
      poll();
      return;
    }

    const script = document.createElement("script");
    script.src = GSI_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => {
      _gsiLoadPromise = null;
      reject(new Error("Failed to load Google sign-in"));
    };
    document.head.appendChild(script);
  });

  return _gsiLoadPromise;
}

interface GoogleSignInButtonProps {
  onCredential: (idToken: string) => void;
  disabled?: boolean;
}

export function GoogleSignInButton({
  onCredential,
  disabled,
}: GoogleSignInButtonProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

  // Keep a stable ref to the latest onCredential so the effect doesn't
  // re-run (and re-render the GSI button) on every parent render.
  const onCredentialRef = useRef(onCredential);
  onCredentialRef.current = onCredential;

  useEffect(() => {
    if (!clientId || !containerRef.current) return;

    let cancelled = false;
    const container = containerRef.current;

    void loadGsiScript()
      .then(() => {
        if (cancelled || !containerRef.current) return;

        const id = window.google?.accounts?.id;
        if (!id) return;

        if (!_gsiInitialized) {
          id.initialize({
            client_id: clientId,
            callback: (response) => {
              if (response.credential) onCredentialRef.current(response.credential);
            },
          });
          _gsiInitialized = true;
        }

        // Match the Google button width to the container (which is w-full).
        id.renderButton(container, {
          theme: "outline",
          size: "large",
          width: Math.max(container.offsetWidth, 240),
          text: "continue_with",
        });
      })
      .catch(() => {
        /* Script load failed — silently degrade to email/password only. */
      });

    return () => {
      cancelled = true;
    };
  }, [clientId]); // only re-run when clientId changes, not onCredential

  if (!clientId) return null;

  return (
    <div
      ref={containerRef}
      className={disabled ? "w-full pointer-events-none opacity-50" : "w-full"}
    />
  );
}
