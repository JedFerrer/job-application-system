"use client";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/context/auth-context";

export function SessionUnavailable() {
  const { retrySession } = useAuth();

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div role="alert" className="w-full max-w-sm space-y-4 rounded-lg border bg-card p-6 text-center">
        <h1 className="text-lg font-semibold">Could not verify your session</h1>
        <p className="text-sm text-muted-foreground">
          We could not confirm your sign-in right now. Please try again.
        </p>
        <Button onClick={() => void retrySession()}>Try again</Button>
      </div>
    </div>
  );
}
