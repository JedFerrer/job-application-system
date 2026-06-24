"use client";

import { useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { ApiError, interviewApi } from "@/lib/api";
import type { InterviewPrepResponse, QuestionCategory } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";

const CATEGORY_LABEL: Record<QuestionCategory, string> = {
  technical: "Technical",
  behavioral: "Behavioral",
  system_design: "System design",
  role_specific: "Role-specific",
  culture_fit: "Culture fit",
};

interface InterviewPrepDialogProps {
  jobId: number | null;
  jobTitle?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function InterviewPrepDialog({
  jobId,
  jobTitle,
  open,
  onOpenChange,
}: InterviewPrepDialogProps) {
  const prepMutation = useMutation({
    mutationFn: (force: boolean) =>
      interviewApi.generate(jobId!, {}, force),
  });

  // Fire generation as soon as the dialog opens for a new job.
  // useEffect is required here because `open` is controlled by the parent —
  // Radix UI does not call onOpenChange when the `open` prop changes
  // programmatically, so we cannot rely on a handler to trigger the mutation.
  useEffect(() => {
    if (!open) {
      prepMutation.reset();
      return;
    }
    if (jobId !== null && !prepMutation.data && !prepMutation.isPending) {
      prepMutation.mutate(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, jobId]);

  const prep: InterviewPrepResponse | undefined = prepMutation.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-4" />
            Interview prep
          </DialogTitle>
          <DialogDescription>
            {jobTitle ?? "This role"} — likely questions tailored to your
            profile.
          </DialogDescription>
        </DialogHeader>

        {prepMutation.isPending && (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Generating tailored questions…
          </div>
        )}

        {prepMutation.isError && (
          <p className="py-4 text-sm text-destructive">
            {prepMutation.error instanceof ApiError
              ? prepMutation.error.message
              : "Failed to generate interview prep"}
          </p>
        )}

        {prep && (
          <div className="flex flex-col gap-3">
            {prep.focus_areas.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {prep.focus_areas.map((area) => (
                  <Badge key={area} variant="outline">
                    {area}
                  </Badge>
                ))}
              </div>
            )}
            <ScrollArea className="max-h-[55vh] pr-3">
              <ol className="flex flex-col gap-4">
                {prep.questions.map((q, index) => (
                  <li key={index} className="flex flex-col gap-1">
                    <div className="flex items-start gap-2">
                      <span className="text-sm font-semibold text-muted-foreground">
                        {index + 1}.
                      </span>
                      <div className="flex flex-col gap-1">
                        <p className="text-sm font-medium">{q.question}</p>
                        <Badge variant="secondary" className="w-fit">
                          {CATEGORY_LABEL[q.category] ?? q.category}
                        </Badge>
                        <p className="text-xs text-muted-foreground">
                          <span className="font-medium">Why: </span>
                          {q.why_it_matters}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          <span className="font-medium">Angle: </span>
                          {q.suggested_angle}
                        </p>
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </ScrollArea>
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs text-muted-foreground">
                {prep.cached ? "Cached result" : "Freshly generated"} ·{" "}
                {prep.model}
              </span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => prepMutation.mutate(true)}
                disabled={prepMutation.isPending}
              >
                <RefreshCw className="size-4" />
                Regenerate
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
