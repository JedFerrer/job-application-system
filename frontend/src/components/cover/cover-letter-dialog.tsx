"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { ApiError, coverLettersApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

interface CoverLetterDialogProps {
  jobId: number | null;
  jobTitle?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CoverLetterDialog({
  jobId,
  jobTitle,
  open,
  onOpenChange,
}: CoverLetterDialogProps) {
  const [coverLetterId, setCoverLetterId] = useState<number | null>(null);
  const [content, setContent] = useState("");

  // Reset state whenever the dialog targets a different job.
  useEffect(() => {
    if (open) {
      setCoverLetterId(null);
      setContent("");
    }
  }, [open, jobId]);

  const generateMutation = useMutation({
    mutationFn: (tone?: string) => {
      if (jobId == null) throw new Error("No job selected");
      return coverLettersApi.generate(jobId, tone);
    },
    onSuccess: (cl) => {
      setCoverLetterId(cl.id);
      setContent(cl.content);
      toast.success("Cover letter generated");
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Generation failed"),
  });

  const saveMutation = useMutation({
    mutationFn: () => {
      if (coverLetterId == null) throw new Error("Nothing to save");
      return coverLettersApi.update(coverLetterId, content);
    },
    onSuccess: () => toast.success("Edits saved"),
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Save failed"),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Cover letter</DialogTitle>
          <DialogDescription>
            {jobTitle
              ? `Tailored to "${jobTitle}". Grounded in your active profile.`
              : "Grounded in your active profile."}
          </DialogDescription>
        </DialogHeader>

        {coverLetterId == null ? (
          <div className="flex flex-col items-center gap-3 py-8">
            <p className="text-sm text-muted-foreground">
              Generate a draft, then edit it before saving.
            </p>
            <Button
              onClick={() => generateMutation.mutate(undefined)}
              disabled={generateMutation.isPending}
            >
              {generateMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              {generateMutation.isPending ? "Generating…" : "Generate draft"}
            </Button>
          </div>
        ) : (
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={16}
            className="font-mono text-sm"
          />
        )}

        {coverLetterId != null && (
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => generateMutation.mutate(undefined)}
              disabled={generateMutation.isPending}
            >
              {generateMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              Regenerate
            </Button>
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || content.trim().length === 0}
            >
              {saveMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              Save edits
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
