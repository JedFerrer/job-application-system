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

// Module-level flag: GSI must only be initialized once per page load.
// React StrictMode mounts components twice in development, which would
// call google.accounts.id.initialize() twice and log a GSI_LOGGER warning.
let _gsiInitialized = false;

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

function loadGsiScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${GSI_SRC}"]`)) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = GSI_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load Google sign-in"));
    document.head.appendChild(script);
  });
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

  useEffect(() => {
    if (!clientId || !containerRef.current) return;

    let cancelled = false;
    void loadGsiScript()
      .then(() => {
        const id = window.google?.accounts?.id;
        if (cancelled || !id || !containerRef.current) return;
        if (!_gsiInitialized) {
          id.initialize({
            client_id: clientId,
            callback: (response) => {
              if (response.credential) onCredential(response.credential);
            },
          });
          _gsiInitialized = true;
        }
        id.renderButton(containerRef.current, {
          theme: "outline",
          size: "large",
          width: 320,
          text: "continue_with",
        });
      })
      .catch(() => {
        /* Script load failed — silently degrade to email/password only. */
      });

    return () => {
      cancelled = true;
    };
  }, [clientId, onCredential]);

  if (!clientId) return null;

  return (
    <div
      ref={containerRef}
      className={disabled ? "pointer-events-none opacity-50" : undefined}
    />
  );
}
