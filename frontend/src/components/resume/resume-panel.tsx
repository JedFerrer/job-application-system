"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, ExternalLink, Loader2, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { ApiError, resumeApi } from "@/lib/api";
import { useResumeTask } from "@/context/resume-task-context";
import type { ParsedProfile } from "@/lib/types";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const ACCEPTED = ".pdf,.docx";

export function ResumePanel() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [openDraftId, setOpenDraftId] = useState<number | null>(null);
  const [deleteDraftId, setDeleteDraftId] = useState<number | null>(null);

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

  const draftsQuery = useQuery({
    queryKey: ["resume-drafts"],
    queryFn: resumeApi.listDrafts,
    retry: false,
  });
  const draftDetailQuery = useQuery({
    queryKey: ["resume-draft", openDraftId],
    queryFn: () => resumeApi.getDraft(openDraftId!),
    enabled: openDraftId != null,
    retry: false,
  });
  const deleteDraftMutation = useMutation({
    mutationFn: (draftId: number) => resumeApi.deleteDraft(draftId),
    onSuccess: (_, draftId) => {
      if (openDraftId === draftId) setOpenDraftId(null);
      setDeleteDraftId(null);
      toast.success("Saved draft deleted");
      void queryClient.invalidateQueries({ queryKey: ["resume-drafts"] });
      void queryClient.removeQueries({ queryKey: ["resume-draft", draftId] });
    },
    onError: () => toast.error("Could not delete saved draft."),
  });

  const handlePick = () => fileRef.current?.click();

  const handleFile = (file: File | undefined) => {
    if (!file) return;
    uploadMutation.mutate(file);
  };

  const profile = profileQuery.data?.profile;

  // No active profile yet — backend returns 200 with null body (not 404).
  const noProfile = profileQuery.data === null;

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

      <Card>
        <CardHeader>
          <CardTitle>Saved tailored drafts</CardTitle>
          <CardDescription>
            Saved AI-generated drafts remain user-controlled. Review every claim before use.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {draftsQuery.isLoading && (
            <p className="text-sm text-muted-foreground">Loading saved drafts…</p>
          )}
          {draftsQuery.isError && (
            <p className="text-sm text-destructive">Could not load saved drafts.</p>
          )}
          {draftsQuery.data?.length === 0 && (
            <p className="text-sm text-muted-foreground">No saved tailored drafts yet.</p>
          )}
          {draftsQuery.data && draftsQuery.data.length > 0 && (
            <div className="flex flex-col gap-2">
              {draftsQuery.data.map((draft) => (
                <div
                  key={draft.id}
                  className="flex flex-col gap-3 rounded-md border p-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{draft.job_title}</p>
                    <p className="text-xs text-muted-foreground">
                      {draft.job_company ?? "Unknown company"} · Saved {formatDraftDate(draft.created_at)}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Button size="sm" variant="outline" onClick={() => setOpenDraftId(draft.id)}>
                      <Eye className="h-4 w-4" />
                      Open
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete saved draft for ${draft.job_title}`}
                      onClick={() => setDeleteDraftId(draft.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {openDraftId != null && draftDetailQuery.data && (
        <TailorResumeDialog
          jobId={draftDetailQuery.data.job_id}
          jobTitle={draftDetailQuery.data.job_title}
          open
          readOnly
          initialData={draftDetailQuery.data.content}
          onOpenChange={(open) => {
            if (!open) setOpenDraftId(null);
          }}
        />
      )}
      {openDraftId != null && draftDetailQuery.isLoading && (
        <p className="text-sm text-muted-foreground">Loading saved draft…</p>
      )}
      {openDraftId != null && draftDetailQuery.isError && (
        <p className="text-sm text-destructive">Could not load saved draft.</p>
      )}

      <AlertDialog
        open={deleteDraftId != null}
        onOpenChange={(open) => {
          if (!open) setDeleteDraftId(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete saved draft?</AlertDialogTitle>
            <AlertDialogDescription>
              This saved tailored resume snapshot will be permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteDraftMutation.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (deleteDraftId != null) deleteDraftMutation.mutate(deleteDraftId);
              }}
            >
              {deleteDraftMutation.isPending ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function formatDraftDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
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

        <Section title="Professional links">
          {Object.entries(profile.links ?? {}).some(([, value]) => value) ? (
            <div className="flex flex-wrap gap-3 text-sm">
              {Object.entries(profile.links ?? {}).map(([field, value]) => value && (
                <a key={field} href={value} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 text-primary underline-offset-4 hover:underline">
                  <ExternalLink className="h-3.5 w-3.5" />
                  {field.charAt(0).toUpperCase() + field.slice(1)}
                </a>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">No professional links added.</p>}
        </Section>

        <Section title="Projects">
          {profile.projects.length > 0 ? (
            <div className="flex flex-col gap-4">
              {profile.projects.map((project, index) => (
                <div key={index} className="rounded-md border p-4">
                  <p className="font-medium">{project.name ?? "Project"}</p>
                  {project.description && <p className="mt-1 text-sm text-muted-foreground">{project.description}</p>}
                  {project.technologies.length > 0 && <p className="mt-2 text-xs text-muted-foreground"><span className="font-medium text-foreground">Technologies:</span> {project.technologies.join(", ")}</p>}
                  {project.achievements.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">{project.achievements.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>}
                  {project.url && <a href={project.url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-2 text-sm text-primary underline-offset-4 hover:underline"><ExternalLink className="h-3.5 w-3.5" />Project link</a>}
                </div>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">No projects added.</p>}
        </Section>
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
