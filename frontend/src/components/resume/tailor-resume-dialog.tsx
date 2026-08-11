"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Briefcase, Copy, FileDown, FileText, Loader2, RefreshCw, RotateCcw, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { resumeApi } from "@/lib/api";
import { downloadResumeDocx, downloadResumePdf } from "@/lib/cover-letter-export";
import type { RequirementClassification, TailoredResume } from "@/lib/types";
import { createReviewState, reviewItems, reviewedResume, sourceOnlyResume, wordDiff, type ReviewState } from "@/lib/tailoring-review";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface TailorResumeDialogProps { jobId: number | null; jobTitle?: string; open: boolean; onOpenChange: (open: boolean) => void; initialData?: TailoredResume | null; readOnly?: boolean; }

function dateLabel(start: string | null, end: string | null): string {
  const fmt = (value: string | null) => {
    if (!value) return "";
    const match = value.match(/^(\d{4})-(\d{2})/);
    if (!match) return value;
    return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(match[2]) - 1] ?? match[2]} ${match[1]}`;
  };
  const from = fmt(start);
  const to = end ? fmt(end) : "Present";
  return from || end ? `${from} – ${to}` : "";
}

function formatTailoredText(data: TailoredResume): string {
  const lines: string[] = [];
  if (data.full_name) lines.push(data.full_name.toUpperCase(), "");
  if (data.headline) lines.push(data.headline, "");
  const contact = [data.email, data.phone].filter(Boolean).join(" | ");
  if (contact) lines.push(contact, "");
  const links = data.links ?? {};
  const linkLines = Object.entries(links)
    .filter((entry): entry is [string, string] => Boolean(entry[1]))
    .map(([label, url]) => `${label[0]!.toUpperCase()}${label.slice(1)}: ${url}`);
  if (linkLines.length) lines.push("LINKS", ...linkLines, "");
  if (data.professional_summary) lines.push("PROFESSIONAL SUMMARY", data.professional_summary, "");
  const skills = data.skills ?? [...data.matched_skills, ...data.other_skills];
  if (skills.length) lines.push("TECHNICAL SKILLS", skills.join(", "), "");
  if (data.work_history.length) {
    lines.push("PROFESSIONAL EXPERIENCE", "");
    for (const role of data.work_history) {
      const date = dateLabel(role.start, role.end);
      lines.push(`${role.title || "Role"} | ${role.company || ""}${date ? ` | ${date}` : ""}`);
      lines.push(...role.tailored_highlights.map((highlight) => `  • ${highlight}`), "");
    }
  }
  if (data.projects?.length) {
    lines.push("PROJECTS", "");
    for (const project of data.projects) {
      const title = project.name || "Project";
      lines.push(title);
      if (project.description) lines.push(project.description);
      if (project.technologies.length) lines.push(`Technologies: ${project.technologies.join(", ")}`);
      lines.push(...project.achievements.map((achievement) => `  • ${achievement}`));
      if (project.url) lines.push(`URL: ${project.url}`);
      lines.push("");
    }
  }
  if (data.education.length) { lines.push("EDUCATION"); lines.push(...data.education.map((item) => [item.degree, item.institution, item.year].filter(Boolean).join(" | ")), ""); }
  if (data.certifications.length) lines.push("CERTIFICATIONS", data.certifications.join(" | "), "");
  if (data.licenses?.length) lines.push("LICENSES & CREDENTIALS", ...data.licenses.map((item) => [item.name, item.issuer].filter(Boolean).join(" | ")), "");
  if (data.languages?.length) lines.push("LANGUAGES", ...data.languages.map((item) => [item.language, item.proficiency].filter(Boolean).join(" — ")), "");
  return lines.join("\n").trim();
}

function DiffText({ original, suggestion, mode }: { original: string; suggestion: string; mode: "original" | "suggested" }) {
  return <>{wordDiff(original, suggestion).map((part, index) => mode === "original" ? (part.removed ? <del key={index}>{part.text}</del> : !part.added ? <span key={index}>{part.text}</span> : null) : (part.added ? <mark key={index}>{part.text}</mark> : !part.removed ? <span key={index}>{part.text}</span> : null))}</>;
}

function CoverageGroup({ title, items, titleClassName }: { title: string; items: RequirementClassification[]; titleClassName?: string }) {
  if (items.length === 0) return null;
  return <div><p className={`mb-1 text-xs font-medium ${titleClassName ?? "text-muted-foreground"}`}>{title}</p><div className="flex flex-wrap gap-1.5">{items.map((item) => <Badge key={item.requirement} variant="outline">{item.requirement}</Badge>)}</div></div>;
}

export function TailorResumeDialog({ jobId, jobTitle, open, onOpenChange, initialData = null, readOnly = false }: TailorResumeDialogProps) {
  const queryClient = useQueryClient();
  const [draftData, setDraftData] = useState<TailoredResume | null>(initialData);
  const [reviewState, setReviewState] = useState<ReviewState | null>(null);
  const [coverageAnalysis, setCoverageAnalysis] = useState<RequirementClassification[] | null>(null);
  const generateMutation = useMutation({
    mutationFn: () => { if (jobId == null) throw new Error("No job selected"); return resumeApi.tailorResume(jobId); },
    onSuccess: (content) => setDraftData(content),
    onError: () => toast.error("Could not generate tailored resume."),
  });
  const saveMutation = useMutation({
    mutationFn: (content: TailoredResume) => resumeApi.createDraft({ job_id: jobId!, content }),
    onSuccess: (saved) => {
      toast.success("Tailored resume saved");
      void queryClient.invalidateQueries({ queryKey: ["resume-drafts"] });
      void queryClient.removeQueries({ queryKey: ["resume-draft", saved.id] });
    },
    onError: () => toast.error("Could not save tailored resume draft."),
  });
  const coverageMutation = useMutation({
    mutationFn: (content: TailoredResume) => resumeApi.previewCoverage(content),
    onSuccess: (content) => setCoverageAnalysis(content.requirement_classifications ?? null),
    onError: () => setCoverageAnalysis(null),
  });
  const generatedData = readOnly ? initialData : draftData;
  const items = useMemo(() => generatedData ? reviewItems(generatedData) : null, [generatedData]);
  useEffect(() => { if (open && !readOnly) { generateMutation.reset(); setDraftData(initialData); setReviewState(null); } }, [open, readOnly, jobId]);
  useEffect(() => { if (generatedData && !readOnly && items) setReviewState(createReviewState(generatedData)); }, [generatedData, items, readOnly]);
  useEffect(() => { setCoverageAnalysis(generatedData?.requirement_classifications ?? null); }, [generatedData]);

  const data = generatedData ? (!readOnly && !items ? sourceOnlyResume(generatedData) : reviewState ? reviewedResume(reviewState) : generatedData) : null;
  const candidateSkills = data?.skills ?? (data && !data.requirement_classifications ? [...data.matched_skills, ...data.other_skills] : []);
  const isLoading = !readOnly && draftData == null && generateMutation.isPending;
  const isRegenerating = !readOnly && draftData != null && generateMutation.isPending;
  const generate = () => { if (!readOnly && jobId != null) { generateMutation.reset(); generateMutation.mutate(); } };
  const refreshCoverage = (nextData: TailoredResume) => coverageMutation.mutate(nextData);
  const toggleOriginal = (key: string) => setReviewState((current) => {
    if (!current) return current;
    const rejected = new Set(current.rejected);
    if (rejected.has(key)) rejected.delete(key); else rejected.add(key);
    const nextState = { ...current, rejected };
    refreshCoverage(reviewedResume(nextState));
    return nextState;
  });
  const restoreAll = () => setReviewState((current) => {
    if (!current) return current;
    const nextState = { ...current, rejected: new Set<string>() };
    refreshCoverage(reviewedResume(nextState));
    return nextState;
  });
  const save = () => { if (data && !readOnly) saveMutation.mutate(data); };
  const copy = () => { if (data) navigator.clipboard.writeText(formatTailoredText(data)).then(() => toast.success("Copied to clipboard"), () => toast.error("Copy failed")); };
  const stem = data?.full_name ? `${data.full_name.replace(/\s+/g, "_")}_Resume` : "Tailored_Resume";
  const downloadPdf = () => { if (data) downloadResumePdf(data, stem); };
  const downloadDocx = async () => { if (!data) return; try { await downloadResumeDocx(data, stem); } catch { toast.error("Failed to create Word document"); } };

  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="!flex w-[calc(100%-1rem)] max-h-[90vh] max-w-3xl flex-col overflow-hidden p-4 sm:p-6"><DialogHeader className="shrink-0"><DialogTitle>{readOnly ? "Saved tailored resume" : "Tailor resume"}</DialogTitle><DialogDescription>{readOnly ? "Saved AI-generated draft. Review every claim before use." : jobTitle ? `Review a tailored draft for "${jobTitle}". Generation is not saved until you choose Save resume.` : "Review a tailored draft for this job. Generation is not saved until you choose Save resume."}</DialogDescription></DialogHeader>
    {!data && isLoading ? <div className="flex flex-col items-center justify-center gap-2 py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /><p className="text-sm text-muted-foreground">Generating a reviewable draft...</p></div> : !data ? <div className="flex flex-col items-center gap-3 py-8 text-center"><p className="text-sm text-muted-foreground">{generateMutation.isError ? "Could not generate tailored resume. Try again." : "Generate a tailored resume draft, then review it before saving or using it."}</p><Button onClick={generate} disabled={generateMutation.isPending}><Sparkles className="h-4 w-4" />{generateMutation.isPending ? "Generating..." : "Generate tailored resume"}</Button></div> : <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto pr-1" data-testid="tailor-resume-scroll-region">
        {isRegenerating && <p className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground" role="status"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Regenerating a new unsaved draft. Your current review remains available.</p>}
        {!isRegenerating && generateMutation.isError && <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive" role="alert">Could not regenerate this draft. Your current reviewed resume is unchanged.</p>}
        {coverageAnalysis && coverageAnalysis.length > 0 && <div className="rounded-md border bg-muted/20 p-3" data-testid="fit-coverage-analysis"><h3 className="mb-1 text-sm font-semibold">Candidate Fit and Resume Coverage</h3><p className="mb-3 text-xs text-muted-foreground">Candidate Fit is based on verified profile evidence. Resume Coverage shows what this reviewed resume currently includes. Missing requirements are job context, not candidate claims.</p><div className="grid gap-4 sm:grid-cols-2"><div><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Candidate Fit</p><div className="flex flex-col gap-3 text-sm"><CoverageGroup title="Verified and shown on this resume" items={coverageAnalysis.filter((item) => item.status === "verified" && item.resume_coverage_status === "covered")} /><CoverageGroup title="Verified, not shown on this resume" items={coverageAnalysis.filter((item) => item.status === "verified" && item.resume_coverage_status === "not_represented")} /></div></div><div className="rounded-md border border-amber-300 bg-amber-50/70 p-3 dark:border-amber-400/35 dark:bg-amber-500/10"><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-800 dark:text-amber-200">Job requirements not found in your profile</p><CoverageGroup title="Missing requirements" titleClassName="text-amber-800 dark:text-amber-200" items={coverageAnalysis.filter((item) => item.status === "missing")} /></div></div></div>}
        {coverageAnalysis === null && generatedData?.requirement_classifications && coverageMutation.isError && <p className="text-xs text-muted-foreground">Coverage analysis is temporarily unavailable; your reviewed resume is unchanged.</p>}
        {(data.full_name || data.headline || data.email || data.phone || Object.values(data.links ?? {}).some(Boolean)) && <div>{data.full_name && <h3 className="text-lg font-semibold">{data.full_name}</h3>}{data.headline && <p className="text-sm text-muted-foreground">{data.headline}</p>}{(data.email || data.phone) && <p className="text-sm text-muted-foreground">{[data.email, data.phone].filter(Boolean).join(" | ")}</p>}{Object.values(data.links ?? {}).filter(Boolean).map((url) => <p key={url} className="break-all text-sm text-muted-foreground">{url}</p>)}</div>}
        {candidateSkills.length > 0 && <div><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Candidate skills</h4><div className="flex flex-wrap gap-1.5">{candidateSkills.map((skill) => <Badge key={skill} variant="secondary">{skill}</Badge>)}</div></div>}
        {(!coverageAnalysis || coverageAnalysis.length === 0) && data.matched_skills.length > 0 && <div><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Matched job requirements</h4><div className="flex flex-wrap gap-1.5">{data.matched_skills.map((skill) => <Badge key={skill} variant="secondary">{skill}</Badge>)}</div></div>}
        {data.other_skills.length > 0 && <div><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Other skills</h4><div className="flex flex-wrap gap-1.5">{data.other_skills.map((skill) => <Badge key={skill} variant="outline">{skill}</Badge>)}</div></div>}
        {(!coverageAnalysis || coverageAnalysis.length === 0) && data.missing_keywords.length > 0 && <div className="rounded-md border border-amber-300 bg-amber-50/70 p-3 dark:border-amber-400/35 dark:bg-amber-500/10"><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-800 dark:text-amber-200">Job requirements not found in your profile</h4><div className="flex flex-wrap gap-1.5">{data.missing_keywords.map((keyword) => <Badge key={keyword} variant="outline" className="border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-400/50 dark:bg-amber-500/10 dark:text-amber-200">{keyword}</Badge>)}</div></div>}
        <div><h3 className="mb-2 flex items-center gap-2 text-sm font-semibold"><Briefcase className="h-4 w-4 text-blue-500" />Review work experience</h3><p className="mb-3 text-xs text-muted-foreground">Changed bullets show the original and suggestion. Keep original when you do not want a suggestion included.</p><div className="flex flex-col gap-4">{data.work_history.map((role, roleIndex) => { const roleItems = items?.[roleIndex]; const rejected = reviewState?.rejected ?? new Set<string>(); return <div key={roleIndex} className="rounded-md border p-4"><div className="mb-2 flex items-start justify-between gap-3"><div><p className="text-sm font-medium">{role.title ?? "Role"}</p>{role.company && <p className="text-sm text-muted-foreground">{role.company}</p>}</div><span className="shrink-0 text-xs text-muted-foreground">{dateLabel(role.start, role.end)}</span></div>{roleItems ? <div className="space-y-2">{roleItems.map((item) => { const isRejected = rejected.has(item.key); if (!item.changed) return item.label ? <div key={item.key} className="flex items-center gap-2"><Badge variant="secondary">{item.label}</Badge><p className="text-sm leading-relaxed text-muted-foreground">• {item.suggestionText}</p></div> : <p key={item.key} className="text-sm leading-relaxed text-muted-foreground">• {item.suggestionText}</p>; return <div key={item.key} data-testid={`review-item-${item.key}`} className="rounded-md bg-muted/30 p-2.5"><div className="mb-1 flex flex-wrap items-center gap-2"><Badge variant={isRejected ? "outline" : "secondary"}>{isRejected ? "Original kept" : item.label}</Badge>{!readOnly && <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => toggleOriginal(item.key)} aria-label={`${isRejected ? "Use suggestion" : "Keep original"} for bullet ${item.outputIndex + 1}`}>{isRejected ? <RotateCcw className="mr-1 h-3.5 w-3.5" /> : null}{isRejected ? "Use suggestion" : "Keep original"}</Button>}</div><p className="text-xs text-muted-foreground"><span className="font-medium">Original:</span> <DiffText original={item.originalText} suggestion={item.suggestionText} mode="original" /></p><p className="text-sm leading-relaxed"><span className="font-medium">Suggestion:</span> <DiffText original={item.originalText} suggestion={item.suggestionText} mode="suggested" /></p></div>; })}</div> : <div><p className="rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800">Comparison unavailable; showing source bullets safely.</p><ul className="mt-2 space-y-1.5">{role.original_highlights.map((highlight, index) => <li key={index} className="text-sm leading-relaxed text-muted-foreground">• {highlight}</li>)}</ul></div>}</div>; })}</div></div>
        {!readOnly && reviewState && reviewState.rejected.size > 0 && <Button size="sm" variant="outline" onClick={restoreAll}><RotateCcw className="h-4 w-4" />Use all suggestions</Button>}
        <div className="border-t pt-2"><p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Other resume content</p>{data.projects?.length ? <div className="mb-4"><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Projects</h4><div className="flex flex-col gap-3">{data.projects.map((project, index) => <div key={`${project.name ?? "project"}-${index}`} className="text-sm"><p className="font-medium">{project.name ?? "Project"}</p>{project.description && <p className="text-muted-foreground">{project.description}</p>}{project.technologies.length > 0 && <p className="text-muted-foreground">Technologies: {project.technologies.join(", ")}</p>}{project.achievements.map((achievement) => <p key={achievement}>• {achievement}</p>)}{project.url && <p className="break-all text-muted-foreground">{project.url}</p>}</div>)}</div></div> : null}{data.education.length > 0 && <div className="mb-4"><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Education</h4><div className="flex flex-col gap-2">{data.education.map((item, index) => <div key={index} className="flex items-start justify-between gap-3 text-sm"><span>{[item.degree, item.institution].filter(Boolean).join(" — ")}</span>{item.year && <span className="shrink-0 text-xs text-muted-foreground">{item.year}</span>}</div>)}</div></div>}{data.certifications.length > 0 && <div className="mb-4"><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Certifications</h4><div className="flex flex-wrap gap-1.5">{data.certifications.map((certification) => <Badge key={certification} variant="outline">{certification}</Badge>)}</div></div>}{data.licenses?.length ? <div className="mb-4"><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Licenses & credentials</h4><div className="flex flex-col gap-1 text-sm">{data.licenses.map((item, index) => <p key={`${item.name}-${index}`}>{[item.name, item.issuer].filter(Boolean).join(" — ")}</p>)}</div></div> : null}{data.languages?.length ? <div><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Languages</h4><div className="flex flex-wrap gap-1.5">{data.languages.map((item, index) => <Badge key={`${item.language}-${index}`} variant="outline">{[item.language, item.proficiency].filter(Boolean).join(" — ")}</Badge>)}</div></div> : null}</div>
      </div>
      <div className="mt-0 flex shrink-0 flex-wrap gap-2 border-t bg-background/95 pt-4 backdrop-blur" data-testid="tailor-resume-actions"><Button size="sm" onClick={copy}><Copy className="h-4 w-4" />Copy all</Button><Button size="sm" variant="outline" onClick={downloadPdf}><FileDown className="h-4 w-4" />PDF</Button><Button size="sm" variant="outline" onClick={downloadDocx}><FileText className="h-4 w-4" />DOCX</Button>{!readOnly && <><Button size="sm" onClick={save} disabled={saveMutation.isPending}>{saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}Save resume</Button><Button size="sm" variant="outline" onClick={generate} disabled={generateMutation.isPending}><RefreshCw className="h-4 w-4" />Regenerate draft</Button></>}</div>
    </div>}
  </DialogContent></Dialog>;
}
