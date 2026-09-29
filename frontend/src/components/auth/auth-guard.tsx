"use client";

/**
 * Client-side route guard for authenticated areas.
 *
 * Auth uses an in-memory access token (no readable cookie), so Next.js
 * middleware cannot inspect it on the edge — gating must happen on the client.
 * While the session provider is bootstrapping (silent refresh) we show a
 * spinner; once resolved we render children, redirect, or offer a manual retry.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { SessionUnavailable } from "@/components/auth/session-unavailable";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/signin");
    }
  }, [status, router]);

  if (status === "unavailable") return <SessionUnavailable />;

  if (status !== "authenticated") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return <>{children}</>;
}
