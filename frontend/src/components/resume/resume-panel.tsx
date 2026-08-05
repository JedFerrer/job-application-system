"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { resumeApi } from "@/lib/api";
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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

export function ResumePanel() {
  const queryClient = useQueryClient();
  const [openDraftId, setOpenDraftId] = useState<number | null>(null);
  const [deleteDraftId, setDeleteDraftId] = useState<number | null>(null);

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

  const draftCount = draftsQuery.data?.length;
  const summary =
    draftCount == null
      ? "Saved job-specific snapshots remain user-controlled. Review every claim before use."
      : `${draftCount} saved ${draftCount === 1 ? "resume" : "resumes"}. Review every claim before use.`;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Saved tailored resumes</CardTitle>
        <CardDescription>{summary}</CardDescription>
      </CardHeader>
      <CardContent>
        {draftsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">Loading saved resumes...</p>
        )}
        {draftsQuery.isError && (
          <p className="text-sm text-destructive">Could not load saved resumes.</p>
        )}
        {draftsQuery.data?.length === 0 && (
          <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed p-6 sm:items-center sm:text-center">
            <div>
              <p className="font-medium">No tailored resumes yet</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Create a tailored resume from a job match, then save it here for review and export.
              </p>
            </div>
            <Link
              href="/matches"
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              Browse job matches
            </Link>
          </div>
        )}
        {draftsQuery.data && draftsQuery.data.length > 0 && (
          <div className="flex flex-col gap-3">
            {draftsQuery.data.map((draft) => (
              <div
                key={draft.id}
                className="group flex flex-col gap-4 rounded-lg border p-4 transition-colors hover:bg-muted/30 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{draft.job_title}</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {draft.job_company ?? "Unknown company"}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Saved {formatDraftDate(draft.created_at)}
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
        <p className="px-6 pb-6 text-sm text-muted-foreground">Loading saved resume...</p>
      )}
      {openDraftId != null && draftDetailQuery.isError && (
        <p className="px-6 pb-6 text-sm text-destructive">Could not load saved resume.</p>
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
              {deleteDraftMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              {deleteDraftMutation.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

function formatDraftDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}
