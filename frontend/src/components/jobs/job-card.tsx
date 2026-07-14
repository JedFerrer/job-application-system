"use client";

import { type ReactNode, useState } from "react";
import {
  Archive,
  ArrowUpRight,
  Building2,
  Calendar,
  Check,
  Globe,
  MapPin,
  Puzzle,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { stripMarkdown } from "@/lib/utils";
import { formatSalary, formatRelativeDate } from "@/lib/format";
import type { MatchReadiness, MatchReadinessDecision } from "@/lib/types";

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
  companyLogo: string | null;
}

export interface JobCardMatch {
  score: number;
  confidence: string | null;       // "strong" | "moderate" | "stretch" | "weak"
  matchedSkills: string[] | null;
  readiness?: MatchReadiness | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SOURCE_LABELS: Record<string, string> = {
  indeed: "Indeed",
  linkedin: "LinkedIn",
  glassdoor: "Glassdoor",
  google: "Google Jobs",
  zip_recruiter: "ZipRecruiter",
};

/** Simple branded icon per job source — recognisable 14×14 SVG marks. */
function SourceIcon({ source }: { source: string }) {
  const s = source.toLowerCase();
  // LinkedIn — blue rounded square, white "in"
  if (s === "linkedin") return <BrandLinkedIn />;
  // Indeed — blue background, white "i"
  if (s === "indeed") return <BrandIndeed />;
  // Glassdoor — green circle
  if (s === "glassdoor") return <BrandGlassdoor />;
  // Google Jobs — blue "G"
  if (s === "google" || s === "google_jobs") return <BrandGoogle />;
  // ZipRecruiter — green
  if (s === "zip_recruiter") return <BrandZipRecruiter />;
  // JobStreet
  if (s === "jobstreet") return <BrandJobStreet />;
  // Generic fallback
  if (s === "manual") return <Building2 className="h-3 w-3 shrink-0 text-muted-foreground/60" />;
  if (s === "extension") return <Puzzle className="h-3 w-3 shrink-0 text-muted-foreground/60" />;
  return <Globe className="h-3 w-3 shrink-0 text-muted-foreground/60" />;
}

function BrandLinkedIn() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 shrink-0">
      <rect width="14" height="14" rx="2.5" fill="#0A66C2" />
      <text x="7" y="10.5" textAnchor="middle" fill="white" fontSize="8.5" fontWeight="600" fontFamily="system-ui, sans-serif">in</text>
    </svg>
  );
}

function BrandIndeed() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 shrink-0">
      <rect width="14" height="14" rx="2.5" fill="#2164F3" />
      <text x="7" y="10.5" textAnchor="middle" fill="white" fontSize="9" fontWeight="700" fontFamily="system-ui, sans-serif">i</text>
    </svg>
  );
}

function BrandGlassdoor() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 shrink-0">
      <circle cx="7" cy="7" r="7" fill="#0CAA41" />
      <text x="7" y="10" textAnchor="middle" fill="white" fontSize="8" fontWeight="700" fontFamily="system-ui, sans-serif">G</text>
    </svg>
  );
}

function BrandGoogle() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 shrink-0">
      <rect width="14" height="14" rx="2.5" fill="#4285F4" />
      <text x="7" y="10.5" textAnchor="middle" fill="white" fontSize="8.5" fontWeight="700" fontFamily="system-ui, sans-serif">G</text>
    </svg>
  );
}

function BrandZipRecruiter() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 shrink-0">
      <rect width="14" height="14" rx="2.5" fill="#2D8CFF" />
      <text x="7" y="10.5" textAnchor="middle" fill="white" fontSize="8" fontWeight="700" fontFamily="system-ui, sans-serif">Z</text>
    </svg>
  );
}

function BrandJobStreet() {
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 shrink-0">
      <rect width="14" height="14" rx="2.5" fill="#1C3FAA" />
      <text x="7" y="10.5" textAnchor="middle" fill="white" fontSize="7" fontWeight="700" fontFamily="system-ui, sans-serif">JS</text>
    </svg>
  );
}

export const CONFIDENCE_LABEL: Record<string, string> = {
  strong: "Strong fit",
  moderate: "Medium fit",
  stretch: "Stretch fit",
  weak: "Low fit",
};

export const CONFIDENCE_COLOR: Record<string, string> = {
  strong: "stroke-emerald-400",
  moderate: "stroke-amber-400",
  stretch: "stroke-orange-400",
  weak: "stroke-red-400",
};

export const CONFIDENCE_TEXT: Record<string, string> = {
  strong: "text-emerald-400",
  moderate: "text-amber-400",
  stretch: "text-orange-400",
  weak: "text-red-400",
};

const MAX_VISIBLE_SKILLS = 4;

const READINESS_LABELS: Record<MatchReadinessDecision, string> = {
  apply: "Ready to apply",
  save: "Save for review",
  stretch: "Stretch",
  skip: "Likely skip",
};

const READINESS_BADGE_CLASS: Record<MatchReadinessDecision, string> = {
  apply:
    "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300",
  save:
    "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300",
  stretch:
    "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300",
  skip:
    "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-500/30 dark:bg-slate-500/10 dark:text-slate-300",
};

/** Derive a 1-2 char avatar from the company name. */
function companyInitials(company: string | null): string {
  if (!company) return "?";
  const parts = company.trim().split(/\s+/);
  if (parts.length >= 2 && parts[0] && parts[1]) return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
  return (company.charAt(0) ?? "?").toUpperCase();
}

/** Simple hash to pick a background colour for the company avatar. */
function avatarBg(name: string): string {
  const colors = [
    "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
    "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    "bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300",
    "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300",
  ];
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return colors[Math.abs(hash) % colors.length]!;
}

// ---------------------------------------------------------------------------
// Circular score ring (SVG)
// ---------------------------------------------------------------------------

export function CircularScore({
  score,
  confidence,
  size = "sm",
}: {
  score: number;
  confidence: string | null;
  size?: "sm" | "lg";
}) {
  const pct = Math.round(score);
  const isLg = size === "lg";
  const radius = isLg ? 34 : 28;
  const svgSize = isLg ? 88 : 72;
  const textSize = isLg ? "text-xl" : "text-lg";
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;
  const strokeColor = confidence ? CONFIDENCE_COLOR[confidence] ?? "stroke-muted-foreground" : "stroke-muted-foreground";
  const textColor = confidence ? CONFIDENCE_TEXT[confidence] ?? "text-muted-foreground" : "text-muted-foreground";

  return (
    <div className="flex flex-col items-center gap-1 shrink-0">
      <div className="relative" style={{ width: svgSize, height: svgSize }}>
        <svg width={svgSize} height={svgSize} viewBox={`0 0 ${svgSize} ${svgSize}`} className="-rotate-90">
          <circle
            cx={svgSize / 2} cy={svgSize / 2} r={radius}
            fill="none"
            strokeWidth="5"
            className="stroke-muted/30"
          />
          <circle
            cx={svgSize / 2} cy={svgSize / 2} r={radius}
            fill="none"
            strokeWidth="5"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            className={`${strokeColor} transition-[stroke-dashoffset] duration-700 ease-out`}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={`${textSize} font-bold tabular-nums leading-none ${textColor}`}>
            {pct}%
          </span>
          <span className="text-[10px] text-white leading-tight mt-0.5">Match</span>
        </div>
      </div>
      {confidence && (
        <span className="text-[11px] text-white">
          {CONFIDENCE_LABEL[confidence] ?? confidence}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface JobCardProps {
  job: JobCardData;
  /** Match data — only shown when provided. */
  match?: JobCardMatch | null;
  /** Top-right label badge. E.g. "Global", "Manual Import". */
  sourceLabel?: string | null;
  /** Optional action rendered in the top-right button area. */
  topRightAction?: ReactNode;
  /** Hide the external-link footer when true. */
  hideExternalLink?: boolean;
  /** Show "Analyzing…" instead of match score. */
  isAnalyzing?: boolean;
  /** If true, the save action shows a "Saved" state. */
  isSaved?: boolean;
  /** Archive click handler (shows archive icon when provided). */
  onArchive?: () => void;
  /** Called when the card body is clicked — used to open the detail page. */
  onCardClick?: () => void;
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
  onCardClick,
  children,
}: JobCardProps) {
  const [logoError, setLogoError] = useState(false);
  const postedLabel = job.postedAt ? formatRelativeDate(job.postedAt) : null;
  const importedLabel = job.importedAt
    ? `Imported ${formatRelativeDate(job.importedAt)}`
    : null;
  const salary = formatSalary(job.salaryMin, job.salaryMax, job.salaryCurrency);
  const initials = companyInitials(job.company);
  const avatarClass = avatarBg(job.company ?? "");

  const visibleSkills = match?.matchedSkills?.slice(0, MAX_VISIBLE_SKILLS) ?? [];
  const extraCount = (match?.matchedSkills?.length ?? 0) - MAX_VISIBLE_SKILLS;

  return (
    <Card
      className={onCardClick ? "group transition-shadow hover:shadow-md cursor-pointer" : "group transition-shadow hover:shadow-md"}
      onClick={onCardClick}
    >
      <CardContent className="p-5">
        <div className="flex items-start gap-4">
          {/* ── Left: all job info ──────────────────────────── */}
          <div className="flex items-start gap-3 min-w-0 flex-1">
            {/* Company avatar — logo image if available, otherwise initials */}
            {job.companyLogo && !logoError ? (
              <img
                src={job.companyLogo}
                alt={job.company ?? "Company"}
                className="hidden sm:block w-10 h-10 rounded-lg shrink-0 object-contain bg-muted/30"
                onError={() => setLogoError(true)}
              />
            ) : (
              <div className={`hidden sm:flex items-center justify-center w-10 h-10 rounded-lg shrink-0 text-sm font-bold ${avatarClass}`}>
                {initials}
              </div>
            )}

            <div className="min-w-0 flex-1">
              <h3 className="text-base font-semibold leading-snug truncate">
                {job.title}
              </h3>
              <p className="text-sm text-muted-foreground mt-0.5">
                {job.company ?? "Unknown company"}
              </p>

              {/* Metadata pills */}
              <div className="flex flex-wrap gap-1.5 mt-2.5">
                <Badge variant="outline" className="rounded-full font-normal text-[11px] gap-1">
                  <SourceIcon source={job.source} />
                  {SOURCE_LABELS[job.source] ?? job.source}
                </Badge>
                {job.location && (
                  <Badge variant="outline" className="rounded-full font-normal text-[11px] gap-1 border-slate-200 dark:border-slate-500/30 bg-slate-50 dark:bg-slate-500/10 text-slate-700 dark:text-slate-300">
                    <MapPin className="h-3 w-3" />
                    {job.location}
                  </Badge>
                )}
                {job.isRemote && (
                  <Badge variant="outline" className="rounded-full font-normal text-[11px] border-indigo-200 dark:border-indigo-500/30 bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-300">
                    Remote
                  </Badge>
                )}
                {job.jobType && (
                  <Badge variant="outline" className="rounded-full font-normal text-[11px] capitalize border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-300">
                    {job.jobType}
                  </Badge>
                )}
                {salary && (
                  <Badge variant="outline" className="rounded-full font-normal text-[11px] border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">
                    {salary}
                  </Badge>
                )}
                <Badge variant="outline" className="rounded-full font-normal text-[11px] gap-1">
                  <Calendar className="h-3 w-3" />
                  {postedLabel ?? "Recently"}
                </Badge>
                {sourceLabel && (
                  <Badge variant="secondary" className="rounded-full font-normal text-[11px]">
                    {sourceLabel}
                  </Badge>
                )}
              </div>

              {/* Description + matched skills */}
              {job.description && (
                <p className="text-sm text-muted-foreground/70 line-clamp-2 leading-relaxed mt-3">
                  {stripMarkdown(job.description)}
                </p>
              )}
              {match && !isAnalyzing && visibleSkills.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 mt-2.5">
                  {visibleSkills.map((skill) => (
                    <Badge key={skill} variant="secondary" className="text-[11px] font-normal gap-1">
                      <Check className="h-3 w-3 text-emerald-500" />
                      {skill}
                    </Badge>
                  ))}
                  {extraCount > 0 && (
                    <Badge variant="outline" className="text-[11px] font-normal">
                      +{extraCount} more
                    </Badge>
                  )}
                </div>
              )}
              {isAnalyzing && (
                <Badge variant="secondary" className="text-[11px] animate-pulse font-normal mt-1.5">
                  Analyzing…
                </Badge>
              )}
              {importedLabel && (
                <p className="text-xs text-muted-foreground/50 mt-1">{importedLabel}</p>
              )}
              {isSaved && (
                <p className="text-xs text-muted-foreground mt-1.5">✓ Saved to applications</p>
              )}
            </div>
          </div>

          {/* ── Right: save + match score ───────────────────── */}
          <div className="flex flex-col items-center gap-3 shrink-0 w-24" onClick={(e) => e.stopPropagation()}>
            <div className="min-h-7 flex items-center">{topRightAction}</div>
            {onArchive && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onArchive(); }}
                title="Archive job"
                className="text-muted-foreground/60 hover:text-foreground transition-colors"
              >
                <Archive className="h-4 w-4" />
              </button>
            )}
            {match && !isAnalyzing && (
              <div className="mt-2 rounded-lg border p-2">
                <CircularScore score={match.score} confidence={match.confidence} />
                {match.readiness && (
                  <div className="mt-2 flex justify-center">
                    <Badge
                      variant="outline"
                      className={`rounded-full font-normal text-[11px] ${READINESS_BADGE_CLASS[match.readiness.decision]}`}
                    >
                      {READINESS_LABELS[match.readiness.decision]}
                    </Badge>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* ── Actions footer ─────────────────────────────────── */}
        {children && (
          <div className="flex flex-wrap items-center gap-2 mt-3 pt-2.5 border-t border-border/40">
            {children}
          </div>
        )}

        {/* ── External link footer ───────────────────────────── */}
        {!hideExternalLink && job.sourceUrl && (
          <div className="mt-3 pt-2.5 border-t border-border/40 ml-0 sm:ml-14">
            <a
              href={job.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground/60 hover:text-muted-foreground transition-colors"
            >
              <ArrowUpRight className="h-3 w-3" />
              Open original job
            </a>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
