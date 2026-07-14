"use client";

import { useQuery } from "@tanstack/react-query";
import { Briefcase, Copy, FileDown, FileText, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { resumeApi } from "@/lib/api";
import { downloadResumePdf, downloadResumeDocx } from "@/lib/cover-letter-export";
import type { TailoredResume } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface TailorResumeDialogProps {
  jobId: number | null;
  jobTitle?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function formatDateNice(start: string | null, end: string | null): string {
  const fmt = (d: string | null) => {
    if (!d) return "";
    // "2025-01-01" → "Jan 2025"
    const m = d.match(/^(\d{4})-(\d{2})/);
    if (m) {
      const months = [
        "Jan","Feb","Mar","Apr","May","Jun",
        "Jul","Aug","Sep","Oct","Nov","Dec",
      ];
      const idx = parseInt(m[2]!, 10) - 1;
      return `${months[idx] || m[2]} ${m[1]}`;
    }
    return d;
  };
  const s = fmt(start);
  const e = end ? fmt(end) : "Present";
  if (!s && !e) return "";
  return `${s} – ${e}`;
}

function formatTailoredText(data: TailoredResume): string {
  const lines: string[] = [];

  if (data.full_name) {
    lines.push(data.full_name.toUpperCase());
    lines.push("");
  }

  const contact = [data.email, data.phone].filter(Boolean).join(" | ");
  if (contact) {
    lines.push(contact);
    lines.push("");
  }

  if (data.professional_summary) {
    lines.push("PROFESSIONAL SUMMARY");
    lines.push(data.professional_summary);
    lines.push("");
  }

  const allSkills = [...data.matched_skills, ...data.other_skills];
  if (allSkills.length > 0) {
    lines.push("TECHNICAL SKILLS");
    lines.push(allSkills.join(", "));
    lines.push("");
  }

  if (data.work_history.length > 0) {
    lines.push("PROFESSIONAL EXPERIENCE");
    lines.push("");
    for (const role of data.work_history) {
      const title = role.title || "Role";
      const company = role.company || "";
      const date = formatDateNice(role.start, role.end);
      lines.push(`${title} | ${company}${date ? ` | ${date}` : ""}`);
      for (const h of role.tailored_highlights) {
        lines.push(`  • ${h}`);
      }
      lines.push("");
    }
  }

  if (data.education.length > 0) {
    lines.push("EDUCATION");
    for (const edu of data.education) {
      const entry = [edu.degree, edu.institution, edu.year].filter(Boolean).join(" | ");
      if (entry) lines.push(entry);
    }
    lines.push("");
  }

  if (data.certifications.length > 0) {
    lines.push("CERTIFICATIONS");
    lines.push(data.certifications.join(" | "));
    lines.push("");
  }

  return lines.join("\n").trim();
}

export function TailorResumeDialog({
  jobId,
  jobTitle,
  open,
  onOpenChange,
}: TailorResumeDialogProps) {
  const tailorQuery = useQuery({
    queryKey: ["tailor-resume", jobId],
    queryFn: () => resumeApi.tailorResume(jobId!),
    enabled: open && jobId != null,
    staleTime: 0,
  });

  const data = tailorQuery.data;
  const isLoading = tailorQuery.isFetching;

  const handleCopy = () => {
    if (!data) return;
    navigator.clipboard.writeText(formatTailoredText(data)).then(
      () => toast.success("Copied to clipboard"),
      () => toast.error("Copy failed"),
    );
  };

  const handleDownloadPdf = () => {
    if (!data) return;
    const stem = data.full_name
      ? `${data.full_name.replace(/\s+/g, "_")}_Resume`
      : "Tailored_Resume";
    downloadResumePdf(data, stem);
  };

  const handleDownloadDocx = async () => {
    if (!data) return;
    const stem = data.full_name
      ? `${data.full_name.replace(/\s+/g, "_")}_Resume`
      : "Tailored_Resume";
    try {
      await downloadResumeDocx(data, stem);
    } catch {
      toast.error("Failed to create Word document");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Tailored resume</DialogTitle>
          <DialogDescription>
            {jobTitle
              ? `Your experience rewritten to emphasize relevance for "${jobTitle}".`
              : "Your experience rewritten for this job."}
            {" "}AI-generated draft based on your profile. Review every claim before copying or downloading.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : !data ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            Could not load tailored resume.
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            {/* Header */}
            {(data.full_name || data.headline) && (
              <div>
                {data.full_name && (
                  <h3 className="text-lg font-semibold">{data.full_name}</h3>
                )}
                {data.headline && (
                  <p className="text-sm text-muted-foreground">{data.headline}</p>
                )}
                {(data.email || data.phone) && (
                  <p className="text-sm text-muted-foreground">
                    {[data.email, data.phone].filter(Boolean).join(" | ")}
                  </p>
                )}
              </div>
            )}

            {/* Skills */}
            {data.matched_skills.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Matched skills
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {data.matched_skills.map((s) => (
                    <Badge key={s} variant="secondary">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {data.other_skills.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Other skills
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {data.other_skills.map((s) => (
                    <Badge key={s} variant="outline">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {data.missing_keywords.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-600">
                  Missing from your profile
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {data.missing_keywords.map((k) => (
                    <Badge key={k} variant="outline" className="border-amber-500 text-amber-600">
                      {k}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* Work history */}
            <div>
              <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Briefcase className="h-4 w-4 text-blue-500" />
                Work experience
              </h4>
              <div className="flex flex-col gap-4">
                {data.work_history.map((role, i) => (
                  <div key={i} className="rounded-md border p-4">
                    <div className="mb-2 flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium">
                          {role.title ?? "Role"}
                        </p>
                        {role.company && (
                          <p className="text-sm text-muted-foreground">
                            {role.company}
                          </p>
                        )}
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {role.start
                          ? `${role.start} – ${role.end ?? "Present"}`
                          : role.end ?? ""}
                      </span>
                    </div>
                    {role.tailored_highlights.length > 0 && (
                      <ul className="space-y-1.5">
                        {role.tailored_highlights.map((h, j) => (
                          <li
                            key={j}
                            className="text-sm leading-relaxed text-muted-foreground"
                          >
                            • {h}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Education */}
            {data.education.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Education
                </h4>
                <div className="flex flex-col gap-2">
                  {data.education.map((edu, i) => (
                    <div
                      key={i}
                      className="flex items-start justify-between gap-3 text-sm"
                    >
                      <span>
                        {[edu.degree, edu.institution].filter(Boolean).join(" — ")}
                      </span>
                      {edu.year && (
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {edu.year}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Certifications */}
            {data.certifications.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Certifications
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {data.certifications.map((c) => (
                    <Badge key={c} variant="outline">
                      {c}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* Footer actions */}
            <div className="flex flex-wrap gap-2 border-t pt-4">
              <Button size="sm" onClick={handleCopy}>
                <Copy className="h-4 w-4" />
                Copy all
              </Button>
              <Button size="sm" variant="outline" onClick={handleDownloadPdf}>
                <FileDown className="h-4 w-4" />
                PDF
              </Button>
              <Button size="sm" variant="outline" onClick={handleDownloadDocx}>
                <FileText className="h-4 w-4" />
                DOCX
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => tailorQuery.refetch()}
                disabled={tailorQuery.isFetching}
              >
                <RefreshCw className="h-4 w-4" />
                Regenerate
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
