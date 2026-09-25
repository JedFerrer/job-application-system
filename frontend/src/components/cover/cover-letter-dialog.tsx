"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
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
import type { CoverLetter } from "@/lib/types";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";

interface CoverLetterDialogProps {
  jobId: number | null;
  jobTitle?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onSaved?: (saved: CoverLetter) => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function CoverLetterDialog({
  jobId,
  jobTitle,
  open,
  onOpenChange,
  onDirtyChange,
  onSaved,
  returnFocusRef,
}: CoverLetterDialogProps) {
  const [coverLetterId, setCoverLetterId] = useState<number | null>(null);
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [closeGuard, setCloseGuard] = useState(false);
  const [pdfExporting, setPdfExporting] = useState(false);
  const pdfExportInFlight = useRef(false);
  const closeAfterSave = useRef(false);
  const dialogContentRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const lastEditorFocusRef = useRef<HTMLElement | null>(null);
  const returnFocusTargetRef = useRef<HTMLElement | null>(null);
  const guardRestoresEditorRef = useRef(true);

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
    setSavedContent("");
  }, [open, jobId]);

  // Populate state when an existing cover letter is returned.
  // Include `open` in deps so this re-fires on reopen even if data reference
  // is unchanged (server returned the same object).
  useEffect(() => {
    if (!open || !existingQuery.data || (coverLetterId != null && content !== savedContent)) return;
    setCoverLetterId(existingQuery.data.id);
    setContent(existingQuery.data.content);
    setSavedContent(existingQuery.data.content);
  }, [existingQuery.data, open]);

  const generateMutation = useMutation({
    mutationFn: (tone?: string) => {
      if (jobId == null) throw new Error("No job selected");
      return coverLettersApi.generate(jobId, tone);
    },
    onSuccess: (cl) => {
      setCoverLetterId(cl.id);
      setContent(cl.content);
      setSavedContent(cl.content);
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
    onSuccess: (saved) => { setSavedContent(saved.content); onSaved?.(saved); toast.success("Edits saved"); if (closeAfterSave.current) { closeAfterSave.current = false; onOpenChange(false); } },
    onError: (err) => { toast.error(err instanceof ApiError ? err.message : "Save failed"); queueMicrotask(focusEditor); },
  });

  const filenameStem = jobTitle
    ? `cover_letter_${jobTitle}`
    : "cover_letter";

  async function handleDownloadPdf() {
    if (!content.trim() || pdfExportInFlight.current) return;
    pdfExportInFlight.current = true;
    setPdfExporting(true);
    try {
      await downloadPdf(content, filenameStem);
    } catch {
      toast.error("Could not create the PDF. Please try again.");
    } finally {
      pdfExportInFlight.current = false;
      setPdfExporting(false);
    }
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
  const dirty = Boolean(open && coverLetterId != null && content !== savedContent);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const isFocusable = (target: HTMLElement | null) => Boolean(target && target.isConnected && !target.matches(":disabled, [hidden], [aria-hidden='true'], [inert]") && !target.closest("[inert]"));
  const focusEditor = () => {
    const fallback = textareaRef.current ?? dialogContentRef.current?.querySelector<HTMLElement>("textarea, button:not(:disabled)") ?? null;
    const target = isFocusable(lastEditorFocusRef.current) ? lastEditorFocusRef.current : fallback;
    if (target && isFocusable(target)) target.focus();
  };
  const focusOpener = () => {
    const target = returnFocusTargetRef.current;
    if (target && isFocusable(target)) target.focus();
  };
  const rememberEditorFocus = (target: EventTarget | null) => {
    if (target instanceof HTMLElement && dialogContentRef.current?.contains(target)) lastEditorFocusRef.current = target;
  };
  const requestOpenChange = (nextOpen: boolean) => { if (!nextOpen && dirty) { rememberEditorFocus(document.activeElement); guardRestoresEditorRef.current = true; setCloseGuard(true); return; } onOpenChange(nextOpen); };
  const discardAndClose = () => { guardRestoresEditorRef.current = false; setContent(savedContent); setCloseGuard(false); onOpenChange(false); };
  const saveAndClose = () => { if (coverLetterId == null) return; guardRestoresEditorRef.current = true; closeAfterSave.current = true; saveMutation.mutate(); };

  return (
    <Dialog open={open} onOpenChange={requestOpenChange}>
      <DialogContent ref={dialogContentRef} className="max-w-2xl" onOpenAutoFocus={() => { returnFocusTargetRef.current = returnFocusRef?.current ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null); }} onCloseAutoFocus={(event) => { event.preventDefault(); focusOpener(); }} onFocusCapture={(event) => rememberEditorFocus(event.target)}>
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
            ref={textareaRef}
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
                disabled={content.trim().length === 0 || pdfExporting}
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
      <AlertDialog open={closeGuard} onOpenChange={setCloseGuard}><AlertDialogContent onCloseAutoFocus={(event) => { event.preventDefault(); if (guardRestoresEditorRef.current) queueMicrotask(focusEditor); }}><AlertDialogHeader><AlertDialogTitle>Save or discard unsaved edits?</AlertDialogTitle><AlertDialogDescription>Your changes are only included after you save them.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={discardAndClose}>Discard</AlertDialogAction><AlertDialogAction disabled={saveMutation.isPending} onClick={saveAndClose}>{saveMutation.isPending ? "Saving…" : "Save"}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    </Dialog>
  );
}
