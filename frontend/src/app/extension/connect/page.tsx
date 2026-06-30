"use client";

/** Extension pairing page.
 *
 * The user opens this page in their browser (they must already be signed in
 * to CareerOS). The page calls the backend to generate a one-time pairing
 * code, which the user then enters in the extension popup.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import { API_URL, setAccessToken as setApiToken } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function ExtensionConnectPage() {
  const { user, status } = useAuth();
  const router = useRouter();
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expiresIn, setExpiresIn] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/signin");
    }
  }, [status, router]);

  const generateCode = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Get access token from the auth context indirectly via the API client
      // We need to make an authenticated request to start pairing
      const accessToken = (await import("@/lib/api")).getAccessToken();
      const res = await fetch(`${API_URL}/extension/auth/start`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        credentials: "include",
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(
          typeof data.detail === "string" ? data.detail : "Failed to generate pairing code",
        );
      }

      const data = await res.json();
      setPairingCode(data.pairing_code);
      setExpiresIn(data.expires_in);

      // Countdown timer
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(() => {
        setExpiresIn((prev) => {
          if (prev <= 1) {
            if (timerRef.current) clearInterval(timerRef.current);
            setPairingCode(null);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to generate pairing code");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const copyCode = useCallback(() => {
    if (pairingCode) {
      void navigator.clipboard.writeText(pairingCode);
    }
  }, [pairingCode]);

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle>Connect Browser Extension</CardTitle>
          <CardDescription>
            Pair the CareerOS browser extension with your account to import jobs
            and autofill applications.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {user && (
            <div className="rounded-md bg-muted px-4 py-2 text-center text-sm">
              Signed in as <strong>{user.email}</strong>
            </div>
          )}

          {!pairingCode && (
            <Button
              onClick={() => void generateCode()}
              disabled={loading}
              className="w-full"
            >
              {loading ? "Generating…" : "Generate Pairing Code"}
            </Button>
          )}

          {pairingCode && (
            <div className="space-y-3">
              <div className="rounded-md bg-accent/10 px-4 py-6 text-center">
                <p className="text-xs text-muted-foreground mb-2">
                  Your pairing code (expires in {expiresIn}s):
                </p>
                <p
                  className="font-mono text-3xl font-bold tracking-[0.3em] cursor-pointer select-all"
                  onClick={copyCode}
                  title="Click to copy"
                >
                  {pairingCode}
                </p>
              </div>

              <Button
                variant="outline"
                onClick={copyCode}
                className="w-full"
              >
                Copy Code
              </Button>

              <div className="rounded-md border p-3 text-xs text-muted-foreground space-y-1">
                <p><strong>Next steps:</strong></p>
                <ol className="list-decimal pl-4 space-y-1">
                  <li>Copy the pairing code above</li>
                  <li>Click the CareerOS extension icon in your browser toolbar</li>
                  <li>Paste the code into the extension popup</li>
                  <li>Click Connect</li>
                </ol>
              </div>

              <Button
                variant="ghost"
                onClick={() => void generateCode()}
                disabled={loading}
                className="w-full text-xs"
              >
                Generate a new code
              </Button>
            </div>
          )}

          {error && (
            <div className="rounded-md bg-destructive/10 px-4 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
