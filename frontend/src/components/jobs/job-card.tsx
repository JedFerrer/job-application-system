"use client";

import { type ReactNode } from "react";
import { Archive, ExternalLink, MapPin } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { stripMarkdown } from "@/lib/utils";
import { formatSalary, formatRelativeDate } from "@/lib/format";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface JobCardData {
  jobId: number;
  title: string;
  company: string | null;
  location: string | null;
  isRemote: boolean | null;
  jobType: string | null;
  shiftType: string | null;
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string | null;
  source: string;
  sourceUrl: string;
  description: string | null;
  postedAt: string | null;
  importedAt: string | null;
}

export interface JobCardMatch {
  score: number;
  confidence: string | null;       // "strong" | "moderate" | "stretch" | "weak"
  matchedSkills: string[] | null;
}

/** Confidence → badge variant mapping. */
const CONFIDENCE_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  strong: "default",
  moderate: "secondary",
  stretch: "outline",
  weak: "destructive",
};

/** Source display name mapping. */
const SOURCE_LABELS: Record<string, string> = {
  indeed: "Indeed",
  linkedin: "LinkedIn",
  glassdoor: "Glassdoor",
  google: "Google Jobs",
  zip_recruiter: "ZipRecruiter",
};

// ---------------------------------------------------------------------------
// Badge color maps — dark-mode-safe semantic colors
// ---------------------------------------------------------------------------

const BASE_PILL = "rounded-full font-normal text-xs";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface JobCardProps {
  job: JobCardData;
  /** Match data — only shown when provided. */
  match?: JobCardMatch | null;
  /** Top-right label badge. E.g. "Global", "Manual Import". */
  sourceLabel?: string | null;
  /** Optional action rendered in the top-right badge area. */
  topRightAction?: ReactNode;
  /** Hide the external-link icon when true. */
  hideExternalLink?: boolean;
  /** Show "Analyzing…" instead of match score. */
  isAnalyzing?: boolean;
  /** If true, the save action shows a "Saved" state. */
  isSaved?: boolean;
  /** Archive click handler (shows archive icon when provided). */
  onArchive?: () => void;
  /** Action buttons rendered in the footer. */
  children?: ReactNode;
}

export function JobCard({
  job,
  match,
  sourceLabel,
  topRightAction,
  hideExternalLink,
  isAnalyzing,
  isSaved,
  onArchive,
  children,
}: JobCardProps) {
  const postedLabel = formatRelativeDate(job.postedAt);
  const importedLabel = job.importedAt
    ? `Imported ${formatRelativeDate(job.importedAt)}`
    : null;
  const salary = formatSalary(job.salaryMin, job.salaryMax, job.salaryCurrency);

  return (
    <Card className="group transition-shadow hover:shadow-md">
      <CardContent className="p-6">
        {/* ── Header ────────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold leading-snug">
              <a
                href={job.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:underline decoration-muted-foreground/30 underline-offset-2"
              >
                {job.title}
              </a>
            </h3>
            <p className="text-sm text-muted-foreground mt-0.5">
              {job.company ?? "Unknown company"}
            </p>
          </div>

          {/* Top-right badges */}
          <div className="flex items-center gap-2 shrink-0">
            {sourceLabel && (
              <Badge variant="secondary" className="text-xs font-normal">
                {sourceLabel}
              </Badge>
            )}
            <Badge variant="outline" className="text-xs font-normal">
              {SOURCE_LABELS[job.source] ?? job.source}
            </Badge>
            {!hideExternalLink && (
              <a
                href={job.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground/60 hover:text-muted-foreground transition-colors"
                title="Open original posting"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
            {topRightAction}
            {onArchive && (
              <button
                type="button"
                onClick={onArchive}
                title="Archive job"
                className="text-muted-foreground/60 hover:text-foreground transition-colors"
              >
                <Archive className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* ── Metadata pills ────────────────────────────────── */}
        <div className="flex flex-wrap gap-1.5 mt-2.5">
          {job.location && (
            <Badge variant="outline" className={`${BASE_PILL} border-slate-200 dark:border-slate-500/30 bg-slate-50 dark:bg-slate-500/10 text-slate-700 dark:text-slate-300`}>
              <MapPin className="h-3 w-3 mr-1" />
              {job.location}
            </Badge>
          )}
          {job.isRemote && (
            <Badge variant="outline" className={`${BASE_PILL} border-indigo-200 dark:border-indigo-500/30 bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-300`}>
              Remote
            </Badge>
          )}
          {job.jobType && (
            <Badge variant="outline" className={`${BASE_PILL} capitalize border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-300`}>
              {job.jobType}
            </Badge>
          )}
          {job.shiftType && (
            <Badge variant="outline" className={`${BASE_PILL} capitalize border-purple-200 dark:border-purple-500/30 bg-purple-50 dark:bg-purple-500/10 text-purple-700 dark:text-purple-300`}>
              {job.shiftType} shift
            </Badge>
          )}
          {salary && (
            <Badge variant="outline" className={`${BASE_PILL} border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300`}>
              {salary}
            </Badge>
          )}
        </div>

        {/* ── Description excerpt ───────────────────────────── */}
        {job.description && (
          <p className="text-sm text-muted-foreground/80 line-clamp-2 leading-relaxed mt-3">
            {stripMarkdown(job.description)}
          </p>
        )}

        {/* ── Timestamps ─────────────────────────────────────── */}
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1.5 text-xs text-muted-foreground/60">
          {postedLabel && <span>Posted {postedLabel}</span>}
          {importedLabel && <span>{importedLabel}</span>}
        </div>

        {/* ── Match insight ──────────────────────────────────── */}
        {isAnalyzing ? (
          <div className="flex items-center gap-2 mt-2.5">
            <Badge variant="secondary" className="text-xs animate-pulse font-normal">
              Analyzing…
            </Badge>
          </div>
        ) : match ? (
          <div className="mt-2.5 flex items-center gap-2 flex-wrap">
            <span className="text-sm font-bold tabular-nums">
              {Math.round(match.score)}%
            </span>
            <span className="text-xs text-muted-foreground">match</span>
            {match.confidence && (
              <Badge
                variant={CONFIDENCE_VARIANT[match.confidence] ?? "outline"}
                className="text-xs font-normal"
              >
                {match.confidence}
              </Badge>
            )}
            {match.matchedSkills && match.matchedSkills.length > 0 && (
              <span className="text-xs text-muted-foreground truncate max-w-70">
                · {match.matchedSkills.slice(0, 4).join(", ")}
              </span>
            )}
          </div>
        ) : null}

        {/* ── Actions footer ─────────────────────────────────── */}
        {children && (
          <div className="flex flex-wrap items-center gap-2 mt-3 pt-2.5 border-t border-border/40">
            {children}
          </div>
        )}

        {/* ── Saved indicator ────────────────────────────────── */}
        {isSaved && (
          <div className="mt-2 text-xs text-muted-foreground">
            ✓ Saved to applications
          </div>
        )}
      </CardContent>
    </Card>
  );
}
