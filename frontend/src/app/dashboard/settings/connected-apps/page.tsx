"use client";

/** Connected Apps settings page.
 *
 * Shows the user's connected browser extension sessions and allows
 * revoking them.
 */

import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor, X } from "lucide-react";
import { toast } from "sonner";
import { ApiError, extensionApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function ConnectedAppsPage() {
  const queryClient = useQueryClient();
  const [revoking, setRevoking] = useState<string | null>(null);

  const { data: sessions, isLoading, error } = useQuery({
    queryKey: ["extension-sessions"],
    queryFn: async () => {
      const data = await extensionApi.listSessions();
      return data.sessions;
    },
    refetchOnWindowFocus: true,
  });

  const handleRevoke = useCallback(
    async (sessionId: string) => {
      setRevoking(sessionId);
      try {
        await extensionApi.revokeSession(sessionId);
        toast.success("Session revoked");
        void queryClient.invalidateQueries({ queryKey: ["extension-sessions"] });
      } catch (e) {
        toast.error(e instanceof ApiError ? e.message : "Failed to revoke session");
      } finally {
        setRevoking(null);
      }
    },
    [queryClient],
  );

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Connected Apps</h1>
        <p className="text-sm text-muted-foreground">
          Manage connected browser extensions and third-party integrations.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Monitor className="h-5 w-5" />
            Browser Extensions
          </CardTitle>
          <CardDescription>
            Chrome extensions connected to your CareerOS account for importing jobs
            and autofilling applications.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading && (
            <p className="text-sm text-muted-foreground">Loading sessions…</p>
          )}

          {error && (
            <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
              Failed to load connected apps
            </div>
          )}

          {sessions && sessions.length === 0 && (
            <div className="py-6 text-center text-sm text-muted-foreground">
              <p className="mb-2">No browser extensions connected.</p>
              <p>
                To connect, open{" "}
                <a
                  href="/extension/connect"
                  className="text-accent underline underline-offset-2"
                >
                  the pairing page
                </a>{" "}
                in your browser and follow the instructions.
              </p>
            </div>
          )}

          {sessions && sessions.length > 0 && (
            <div className="space-y-2">
              {sessions.map((session) => (
                <div
                  key={session.id}
                  className="flex items-center justify-between rounded-md border px-4 py-3"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">
                        {session.browser ?? "Chrome"}
                        {session.device_name ? ` on ${session.device_name}` : ""}
                      </span>
                      {!session.revoked ? (
                        <Badge variant="default" className="text-xs">
                          Active
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-xs">
                          Revoked
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Connected{" "}
                      {new Date(session.created_at).toLocaleDateString()}
                      {session.last_used_at &&
                        ` · Last used ${new Date(session.last_used_at).toLocaleDateString()}`}
                    </p>
                  </div>
                  {!session.revoked && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void handleRevoke(session.id)}
                      disabled={revoking === session.id}
                    >
                      <X className="h-4 w-4 mr-1" />
                      {revoking === session.id ? "Revoking…" : "Revoke"}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
