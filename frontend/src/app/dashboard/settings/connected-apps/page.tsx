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
          <div className="mb-6 flex flex-col gap-3 rounded-md border bg-muted/30 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-1">
              <p className="text-sm font-medium">Connect or reconnect Chrome</p>
              <p className="text-sm text-muted-foreground">
                Generate a new pairing code to connect a new extension or reconnect
                one that was revoked.
              </p>
            </div>
            <Button asChild className="shrink-0">
              <a href="/extension/connect">Pair Chrome extension</a>
            </Button>
          </div>

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
                  className="text-primary underline underline-offset-2 hover:text-primary/80"
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
                    {session.revoked && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        This session was revoked. Pair the extension again to reconnect it.
                      </p>
                    )}
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
