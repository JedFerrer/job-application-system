"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [resetUrl, setResetUrl] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => authApi.forgotPassword(email),
    onSuccess: (data) => {
      toast.success("If the email is registered, a reset link has been generated.");
      if (data.reset_url) setResetUrl(data.reset_url);
    },
    onError: () => toast.error("Something went wrong. Please try again."),
  });

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Forgot password</CardTitle>
          <CardDescription>
            Enter your email and we&apos;ll generate a password reset link.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {resetUrl ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">
                Your reset link (valid for 15 minutes):
              </p>
              <a
                href={resetUrl}
                className="text-sm font-medium text-primary underline break-all"
              >
                {resetUrl}
              </a>
              <Button variant="outline" asChild>
                <Link href={resetUrl}>Open reset page</Link>
              </Button>
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (email.trim()) mutation.mutate();
              }}
              className="flex flex-col gap-4"
            >
              <div className="flex flex-col gap-2">
                <Label htmlFor="fp-email">Email</Label>
                <Input
                  id="fp-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                />
              </div>
              <Button type="submit" disabled={mutation.isPending || !email.trim()}>
                {mutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : null}
                Send reset link
              </Button>
            </form>
          )}

          <div className="text-center">
            <Link
              href="/signin"
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to sign in
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
