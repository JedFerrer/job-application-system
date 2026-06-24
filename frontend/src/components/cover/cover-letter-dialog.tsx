"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FileDown, FileText, Loader2, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { downloadDocx, downloadPdf } from "@/lib/cover-letter-export";
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

  // Fetch existing cover letter for this job when dialog opens.
  // staleTime: 0 ensures it refetches each time the dialog is reopened.
  const existingQuery = useQuery({
    queryKey: ["cover-letter-by-job", jobId],
    queryFn: () => coverLettersApi.getByJobId(jobId!),
    enabled: open && jobId != null,
    retry: false,
    staleTime: 0,
  });

  // Clear local state whenever the dialog opens (or targets a new job)
  // so we never show stale content while the query is in-flight.
  useEffect(() => {
    if (!open) return;
    setCoverLetterId(null);
    setContent("");
  }, [open, jobId]);

  // Populate state when an existing cover letter is returned.
  // Include `open` in deps so this re-fires on reopen even if data reference
  // is unchanged (server returned the same object).
  useEffect(() => {
    if (!open || !existingQuery.data) return;
    setCoverLetterId(existingQuery.data.id);
    setContent(existingQuery.data.content);
  }, [existingQuery.data, open]);

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

  const filenameStem = jobTitle
    ? `cover_letter_${jobTitle}`
    : "cover_letter";

  function handleDownloadPdf() {
    if (!content.trim()) return;
    downloadPdf(content, filenameStem);
  }

  async function handleDownloadDocx() {
    if (!content.trim()) return;
    try {
      await downloadDocx(content, filenameStem);
    } catch {
      toast.error("Failed to create Word document");
    }
  }

  const isLoading = existingQuery.isFetching;

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

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : coverLetterId == null ? (
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

        {!isLoading && coverLetterId != null && (
          <DialogFooter className="flex-wrap gap-2 sm:justify-between">
            {/* Left: regenerate */}
            <div className="flex gap-2">
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
            </div>

            {/* Right: export + save */}
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={handleDownloadPdf}
                disabled={content.trim().length === 0}
                title="Download as PDF"
              >
                <FileDown className="h-4 w-4" />
                PDF
              </Button>
              <Button
                variant="outline"
                onClick={handleDownloadDocx}
                disabled={content.trim().length === 0}
                title="Download as Word document"
              >
                <FileText className="h-4 w-4" />
                DOCX
              </Button>
              <Button
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending || content.trim().length === 0}
                title="Save your edits to the database for later access"
              >
                {saveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                Save edits
              </Button>
            </div>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
