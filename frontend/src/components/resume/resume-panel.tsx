"use client";

import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { ApiError, resumeApi } from "@/lib/api";
import { useResumeTask } from "@/context/resume-task-context";
import type { ParsedProfile } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const ACCEPTED = ".pdf,.docx";

export function ResumePanel() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  // Task state is managed globally so polling survives navigation.
  const { setTask, isRunning, progressMsg } = useResumeTask();

  const profileQuery = useQuery({
    queryKey: ["profile"],
    queryFn: resumeApi.getProfile,
    retry: false,
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => resumeApi.upload(file),
    onSuccess: (data, file) => setTask(data.task_id, file.name),
    onError: (err) => toast.error(errorMessage(err)),
  });

  const handlePick = () => fileRef.current?.click();

  const handleFile = (file: File | undefined) => {
    if (!file) return;
    uploadMutation.mutate(file);
  };

  const profile = profileQuery.data?.profile;
  const noProfile =
    profileQuery.isError &&
    profileQuery.error instanceof ApiError &&
    profileQuery.error.status === 404;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Upload résumé</CardTitle>
          <CardDescription>
            PDF or DOCX, up to 10 MB. The active profile is used for matching
            and cover letters. Requires an OpenAI key on the backend.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPTED}
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <div className="flex items-center gap-3">
            <Button onClick={handlePick} disabled={isRunning || uploadMutation.isPending}>
              {isRunning || uploadMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {isRunning ? (progressMsg ?? "Parsing…") : uploadMutation.isPending ? "Uploading…" : "Choose file"}
            </Button>
            {uploadMutation.variables && (
              <span className="text-sm text-muted-foreground">
                {uploadMutation.variables.name}
              </span>
            )}
          </div>
          {isRunning && (
            <p className="text-xs text-muted-foreground">
              Running in the background — you can navigate away and come back;
              your profile will update when done.
            </p>
          )}
        </CardContent>
      </Card>

      {profileQuery.isLoading && (
        <p className="text-sm text-muted-foreground">Loading profile…</p>
      )}

      {noProfile && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No active profile yet. Upload a résumé to get started.
          </CardContent>
        </Card>
      )}

      {profile && <ProfileView profile={profile} />}
    </div>
  );
}

function ProfileView({ profile }: { profile: ParsedProfile }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{profile.full_name ?? "Candidate profile"}</CardTitle>
        {profile.headline && (
          <CardDescription>{profile.headline}</CardDescription>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-wrap gap-3 text-sm text-muted-foreground">
          {profile.email && <span>{profile.email}</span>}
          {profile.phone && <span>{profile.phone}</span>}
          {profile.seniority && <span>Seniority: {profile.seniority}</span>}
          {profile.years_experience != null && (
            <span>{profile.years_experience} yrs experience</span>
          )}
        </div>

        {profile.skills.length > 0 && (
          <Section title="Skills">
            <div className="flex flex-wrap gap-2">
              {profile.skills.map((s) => (
                <Badge key={s} variant="secondary">
                  {s}
                </Badge>
              ))}
            </div>
          </Section>
        )}

        {profile.work_history.length > 0 && (
          <Section title="Work history">
            <ul className="flex flex-col gap-3">
              {profile.work_history.map((w, i) => (
                <li key={`${w.company}-${i}`} className="text-sm">
                  <p className="font-medium">
                    {w.title ?? "Role"}
                    {w.company ? ` · ${w.company}` : ""}
                  </p>
                  {(w.start || w.end) && (
                    <p className="text-xs text-muted-foreground">
                      {w.start ?? "?"} – {w.end ?? "Present"}
                    </p>
                  )}
                  {w.highlights.length > 0 && (
                    <ul className="ml-4 mt-1 list-disc text-muted-foreground">
                      {w.highlights.map((h, j) => (
                        <li key={j}>{h}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {profile.education.length > 0 && (
          <Section title="Education">
            <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
              {profile.education.map((e, i) => (
                <li key={i}>
                  {[e.degree, e.institution, e.year].filter(Boolean).join(" · ")}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {profile.certifications.length > 0 && (
          <Section title="Certifications">
            <div className="flex flex-wrap gap-2">
              {profile.certifications.map((c) => (
                <Badge key={c} variant="outline">
                  {c}
                </Badge>
              ))}
            </div>
          </Section>
        )}
      </CardContent>
    </Card>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </div>
  );
}

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong";
}
