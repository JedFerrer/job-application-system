"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Briefcase, Copy, FileDown, FileText, Loader2, RefreshCw, RotateCcw, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { resumeApi } from "@/lib/api";
import { buildResumeHeaderItems, buildResumeRenderModel, downloadResumeDocx, downloadResumePdf, formatResumeText, resumeSectionHeading } from "@/lib/cover-letter-export";
import type { RequirementClassification, TailoredCanonicalSection, TailoredEvidenceReference, TailoredResume } from "@/lib/types";
import { createReviewState, decisionItems, reviewItems, reviewedResume, sourceOnlyResume, sourceSkillOrder, summaryReview, wordDiff, type ReviewItem, type ReviewState } from "@/lib/tailoring-review";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface TailorResumeDialogProps { jobId: number | null; jobTitle?: string; open: boolean; onOpenChange: (open: boolean) => void; initialData?: TailoredResume | null; readOnly?: boolean; }
type DisplayItem = { text: string; source_id?: string; display_key?: string };
type DisplaySection = { section: TailoredCanonicalSection["section"]; items: DisplayItem[]; heading?: string | null };

function dateLabel(start: string | null, end: string | null): string {
  const fmt = (value: string | null) => { if (!value) return ""; const match = value.match(/^(\d{4})-(\d{2})/); return match ? `${match[1]}-${match[2]}` : value; };
  const from = fmt(start); const to = end ? fmt(end) : "Present"; return from || end ? `${from} – ${to}` : "";
}
function DiffText({ original, suggestion, mode }: { original: string; suggestion: string; mode: "original" | "suggested" }) { return <>{wordDiff(original, suggestion).map((part, index) => mode === "original" ? (part.removed ? <del key={index}>{part.text}</del> : !part.added ? <span key={index}>{part.text}</span> : null) : (part.added ? <mark key={index}>{part.text}</mark> : !part.removed ? <span key={index}>{part.text}</span> : null))}</>; }
function CoverageGroup({ title, items, titleClassName }: { title: string; items: RequirementClassification[]; titleClassName?: string }) { return items.length ? <div><p className={`mb-1 text-xs font-medium ${titleClassName ?? "text-muted-foreground"}`}>{title}</p><div className="flex flex-wrap gap-1.5">{items.map((item) => <Badge key={item.requirement} variant="outline">{item.requirement}</Badge>)}</div></div> : null; }

function EvidenceList({ items, label = "Supporting source evidence" }: { items?: TailoredEvidenceReference[]; label?: string }) {
  if (!items?.length) return null;
  return <details className="mt-2 rounded border bg-muted/20 p-2" data-testid="review-evidence">
    <summary className="cursor-pointer text-xs font-medium">{label} ({items.length})</summary>
    <div className="mt-2 space-y-2">{items.map((item) => <div key={item.evidence_id} className="text-xs">
      <p className="font-medium">{item.section}{item.role_label ? ` · ${item.role_label}` : ""} <span className="font-normal text-muted-foreground">— {item.relevance_label}</span></p>
      <p className="text-muted-foreground">“{item.excerpt}”</p>
    </div>)}</div>
  </details>;
}

function ReviewBullet({ item, included, rejected, readOnly, canOmit, onInclude, onWording }: { item: ReviewItem; included: boolean; rejected: boolean; readOnly: boolean; canOmit: boolean; onInclude: () => void; onWording: () => void }) {
  const wordingControl = item.changed && included;
  return <div data-testid={`review-item-${item.key}`} className="rounded border p-2">
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={included ? (rejected ? "outline" : "secondary") : "outline"}>{included ? (rejected ? "Original kept" : (item.label ?? "Included")) : "Not shown"}</Badge>
      {included && item.recommendedIncluded && <Badge variant="outline">Recommended for this job</Badge>}
      {!readOnly && <Button size="sm" variant="ghost" onClick={onInclude} disabled={!canOmit} aria-label={included ? "Omit bullet" : "Include bullet"}>{included ? "Omit" : "Include"}</Button>}
      {!readOnly && wordingControl && <Button size="sm" variant="ghost" onClick={onWording}>{rejected ? <><RotateCcw className="mr-1 h-3.5 w-3.5" />Use suggestion</> : "Keep original"}</Button>}
    </div>
    {wordingControl ? <><p className="text-xs text-muted-foreground"><b>Original:</b> <DiffText original={item.originalText} suggestion={item.suggestionText} mode="original" /></p><p className="text-sm"><b>Suggestion:</b> <DiffText original={item.originalText} suggestion={item.suggestionText} mode="suggested" /></p></> : <p className="text-sm text-muted-foreground">• {rejected ? item.originalText : item.suggestionText}</p>}
    {wordingControl && <EvidenceList items={item.evidence} />}
  </div>;
}
export function legacyDisplaySections(data: TailoredResume): DisplaySection[] {
  const section = (name: TailoredCanonicalSection["section"], values: string[]) => ({
    section: name,
    items: values.filter(Boolean).map((text, index) => ({ text, display_key: `legacy-${name}-${index}` })),
  });
  return [
    ...(data.email || data.phone || data.full_name ? [section("contact", [data.full_name, data.email, data.phone].filter((value): value is string => Boolean(value)))] : []),
    ...(Object.values(data.links ?? {}).some(Boolean) ? [section("links", Object.values(data.links ?? {}).filter((value): value is string => Boolean(value)))] : []),
    ...(data.projects?.length ? [section("projects", data.projects.flatMap((project) => [project.name, project.description, ...project.technologies, ...project.achievements, project.url].filter((value): value is string => Boolean(value))))] : []),
    ...(data.education.length ? [section("education", data.education.map((item) => [item.degree, item.institution, item.year].filter(Boolean).join(" | ")))] : []),
    ...(data.certifications.length ? [section("certifications", data.certifications)] : []),
    ...(data.licenses?.length ? [section("licenses", data.licenses.map((item) => [item.name, item.issuer].filter(Boolean).join(" | ")))] : []),
    ...(data.languages?.length ? [section("languages", data.languages.map((item) => [item.language, item.proficiency].filter(Boolean).join(" | ")))] : []),
  ];
}

export function displaySections(data: TailoredResume): DisplaySection[] {
  return data.canonical_sections?.length ? data.canonical_sections : legacyDisplaySections(data);
}

export function TailorResumeDialog({ jobId, jobTitle, open, onOpenChange, initialData = null, readOnly = false }: TailorResumeDialogProps) {
  const queryClient = useQueryClient();
  const [draftData, setDraftData] = useState<TailoredResume | null>(initialData);
  const [reviewState, setReviewState] = useState<ReviewState | null>(null);
  const [coverageAnalysis, setCoverageAnalysis] = useState<RequirementClassification[] | null>(null);
  const generateMutation = useMutation({ mutationFn: () => { if (jobId == null) throw new Error("No job selected"); return resumeApi.tailorResume(jobId); }, onSuccess: setDraftData, onError: () => toast.error("Could not generate tailored resume.") });
  const saveMutation = useMutation({ mutationFn: (content: TailoredResume) => resumeApi.createDraft({ job_id: jobId!, content }), onSuccess: (saved) => { toast.success("Tailored resume saved"); void queryClient.invalidateQueries({ queryKey: ["resume-drafts"] }); void queryClient.removeQueries({ queryKey: ["resume-draft", saved.id] }); }, onError: () => toast.error("Could not save tailored resume draft.") });
  const coverageMutation = useMutation({ mutationFn: (content: TailoredResume) => resumeApi.previewCoverage(content), onSuccess: (content) => setCoverageAnalysis(content.requirement_classifications ?? null), onError: () => setCoverageAnalysis(null) });
  const exportValidationMutation = useMutation({ mutationFn: (content: TailoredResume) => resumeApi.validateExport(content) });
  const generatedData = readOnly ? initialData : draftData;
  const items = useMemo(() => generatedData ? reviewItems(generatedData) : null, [generatedData]);
  const allItems = useMemo(() => generatedData ? decisionItems(generatedData) : null, [generatedData]);
  useEffect(() => { if (open && !readOnly) { generateMutation.reset(); setDraftData(initialData); setReviewState(null); } }, [open, readOnly, jobId]);
  useEffect(() => { if (generatedData && items) setReviewState(createReviewState(generatedData)); }, [generatedData, items, readOnly]);
  useEffect(() => setCoverageAnalysis(generatedData?.requirement_classifications ?? null), [generatedData]);
  const data = generatedData ? (!readOnly && !items ? sourceOnlyResume(generatedData) : reviewState ? reviewedResume(reviewState) : generatedData) : null;
  const canonical: DisplaySection[] = data ? displaySections(data) : [];
  const hasCanonical = Boolean(data?.canonical_sections?.length);
  const headerPreview = data ? buildResumeHeaderItems(data) : [];
  const candidateSkills = data?.skills ?? [];
  const skillsSection = canonical.find((section) => section.section === "skills");
  const skillsHeading = hasCanonical ? resumeSectionHeading("skills", skillsSection?.heading) : "Candidate skills";
  const renderedSections = data ? buildResumeRenderModel(data) : [];
  const reviewedRoles = renderedSections.find((section) => section.section === "work_history")?.roles ?? [];
  const blockedForRegeneration = Boolean(data?.requires_regeneration);
  const summarySection = renderedSections.find((section) => section.section === "summary");
  const canonicalSummary = summarySection?.items ?? [];
  const reviewBase = generatedData ?? data;
  const summaryComparison = reviewBase ? summaryReview(reviewBase) : null;
  const sourceSkills = reviewBase ? sourceSkillOrder(reviewBase) : null;
  const skillsReordered = Boolean(sourceSkills && reviewBase?.skills && reviewBase.skills.join("\u0000") !== sourceSkills.texts.join("\u0000"));
  const additionalSections = canonical.filter((section) => !["contact", "links", "summary", "skills", "unclassified"].includes(section.section));
  const unclassifiedSections = canonical.filter((section) => section.section === "unclassified");
  const refreshCoverage = (next: TailoredResume) => coverageMutation.mutate(next);
  const toggleOriginal = (key: string) => setReviewState((current) => { if (!current) return current; const rejected = new Set(current.rejected); if (rejected.has(key)) rejected.delete(key); else rejected.add(key); const next = { ...current, rejected }; refreshCoverage(reviewedResume(next)); return next; });
  const toggleIncluded = (roleIndex: number, sourceIndex: number) => setReviewState((current) => { if (!current) return current; const role = current.source.work_history[roleIndex]; if (!role) return current; const key = `${roleIndex}:${sourceIndex}`; const count = role.selection_decisions?.filter((decision) => current.finalIncluded.has(`${roleIndex}:${decision.source_index}`)).length ?? 0; if (current.finalIncluded.has(key) && count <= 1) return current; const finalIncluded = new Set(current.finalIncluded); if (finalIncluded.has(key)) finalIncluded.delete(key); else finalIncluded.add(key); const next = { ...current, finalIncluded }; refreshCoverage(reviewedResume(next)); return next; });
  const restoreWording = () => setReviewState((current) => { if (!current) return current; const next = { ...current, rejected: new Set<string>(), summaryRejected: false, skillsSourceOrder: false }; refreshCoverage(reviewedResume(next)); return next; });
  const toggleSummary = () => setReviewState((current) => { if (!current) return current; const next = { ...current, summaryRejected: !current.summaryRejected }; refreshCoverage(reviewedResume(next)); return next; });
  const restoreSkillOrder = () => setReviewState((current) => { if (!current) return current; const next = { ...current, skillsSourceOrder: !current.skillsSourceOrder }; refreshCoverage(reviewedResume(next)); return next; });
  const includeAll = () => setReviewState((current) => { if (!current) return current; const finalIncluded = new Set<string>(); current.source.work_history.forEach((role, roleIndex) => (role.selection_decisions ?? []).forEach((decision) => finalIncluded.add(`${roleIndex}:${decision.source_index}`))); const next = { ...current, finalIncluded }; refreshCoverage(reviewedResume(next)); return next; });
  const generate = () => { if (readOnly || jobId == null) return; const dirty = Boolean(reviewState?.summaryRejected || reviewState?.skillsSourceOrder) || (reviewState?.source.work_history.some((role, roleIndex) => (role.selection_decisions ?? []).some((decision) => reviewState.finalIncluded.has(`${roleIndex}:${decision.source_index}`) !== decision.final_included || reviewState.rejected.has(`${roleIndex}:${decision.source_index}`))) ?? false); if (dirty && !window.confirm("Regenerate and discard your unsaved review choices?")) return; generateMutation.reset(); generateMutation.mutate(); };
  const ensureExportable = async (): Promise<TailoredResume | null> => { if (!data) return null; if (data.requires_regeneration) { toast.error("This resume must be re-generated after the source resume changes."); return null; } const sourceBacked = data.tailoring_snapshot_version === "lossless-complete-v2" || data.tailoring_snapshot_version === "full-context-v1"; if (data.canonical_sections?.length && data.tailoring_snapshot_version === "full-resume-v1") { toast.error("This saved resume must be re-generated before export."); return null; } if (!sourceBacked) return data; try { return await exportValidationMutation.mutateAsync(data); } catch { toast.error("This resume must be re-generated after the source resume changes."); return null; } };
  const copy = async () => { const validated = await ensureExportable(); if (validated) navigator.clipboard.writeText(formatResumeText(validated)).then(() => toast.success("Copied to clipboard"), () => toast.error("Copy failed")); };
  const exportPdf = async () => { const validated = await ensureExportable(); if (validated) downloadResumePdf(validated, stem); };
  const exportDocx = async () => { const validated = await ensureExportable(); if (validated) await downloadResumeDocx(validated, stem); };
  const save = () => { if (data && !readOnly) saveMutation.mutate(data); };
  const stem = data?.full_name ? `${data.full_name.replace(/\s+/g, "_")}_Resume` : "Tailored_Resume";
  const renderWorkHistory = () => <div data-testid="reviewed-work-history"><h3 className="mb-2 flex items-center gap-2 text-sm font-semibold"><Briefcase className="h-4 w-4 text-blue-500" />Review work experience</h3><p className="mb-3 text-xs text-muted-foreground">Omitted bullets remain available to restore; each non-empty role keeps at least one bullet.</p>{data?.work_history.map((role, roleIndex) => { const rendered = reviewedRoles[roleIndex]; const roleItems = allItems?.[roleIndex] ?? []; const outputOrder = new Map((rendered?.bullets ?? []).map((bullet, index) => [bullet.sourceIndex, index])); const included = roleItems.filter((item) => reviewState?.finalIncluded.has(item.key) ?? item.included).sort((a, b) => (outputOrder.get(a.sourceIndex) ?? Number.MAX_SAFE_INTEGER) - (outputOrder.get(b.sourceIndex) ?? Number.MAX_SAFE_INTEGER)); const omitted = roleItems.filter((item) => !(reviewState?.finalIncluded.has(item.key) ?? item.included)); const render = (item: ReviewItem) => <ReviewBullet key={item.key} item={item} included={reviewState?.finalIncluded.has(item.key) ?? item.included ?? false} rejected={reviewState?.rejected.has(item.key) ?? false} readOnly={readOnly} canOmit={(reviewState?.finalIncluded.has(item.key) ?? item.included ?? false) ? included.length > 1 : true} onInclude={() => toggleIncluded(roleIndex, item.sourceIndex)} onWording={() => toggleOriginal(item.key)} />; const context = rendered?.narrative ?? null; const heading = rendered?.responsibilityHeading ?? null; return <div key={roleIndex} className="mb-4 rounded-md border p-4"><p className="mb-2 text-sm font-medium">{rendered?.header || [role.title, role.company].filter(Boolean).join(" | ") || "Role"}</p>{rendered?.header ? null : <p className="mb-2 text-xs text-muted-foreground">{dateLabel(role.start, role.end)}</p>}{context ? <p className="mb-2 rounded bg-muted/30 px-3 py-2 text-sm" data-testid={`role-context-${roleIndex}`}>{context}</p> : null}{heading ? <p className="mb-2 text-xs font-semibold uppercase tracking-wide" data-testid={`responsibilities-heading-${roleIndex}`}>{heading}</p> : null}{!roleItems.length && <p className="rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800">Comparison unavailable; showing source bullets safely.</p>}{included.map(render)}{omitted.length ? <details className="mt-2 rounded border p-2"><summary className="cursor-pointer text-xs font-medium">Not shown on this resume</summary><div className="mt-2 space-y-2">{omitted.map(render)}</div></details> : null}</div>; })}</div>;
  const renderAdditionalSection = (section: (typeof additionalSections)[number], sectionIndex: number) => <div key={`${section.section}-${sectionIndex}-${section.items[0]?.source_id ?? section.items[0]?.display_key ?? "empty"}`} className="mb-3"><h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{hasCanonical ? resumeSectionHeading(section.section, section.heading) : section.section === "named" ? section.heading || "Additional information" : section.section}</h4>{section.items.map((item, itemIndex) => <p key={`${item.source_id ?? item.display_key}-${itemIndex}`} className="text-sm">{item.text}</p>)}</div>;
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="!flex w-[calc(100%-1rem)] max-h-[90vh] max-w-3xl flex-col overflow-hidden p-4 sm:p-6"><DialogHeader className="shrink-0"><DialogTitle>{readOnly ? "Saved tailored resume" : "Tailor resume"}</DialogTitle><DialogDescription>{readOnly ? "Saved AI-generated draft. Review every claim before use." : jobTitle ? `Review a tailored draft for "${jobTitle}". Generation is not saved until you choose Save resume.` : "Review a tailored draft for this job. Generation is not saved until you choose Save resume."}</DialogDescription></DialogHeader>
    {!data && generateMutation.isPending ? <div className="flex flex-col items-center justify-center gap-2 py-12"><Loader2 className="h-6 w-6 animate-spin" /><p>Generating a reviewable draft...</p></div> : !data ? <div className="flex flex-col items-center gap-3 py-8 text-center"><p>{generateMutation.isError ? "Could not generate tailored resume. Try again." : "Generate a tailored resume draft, then review it before saving or using it."}</p><Button onClick={generate} disabled={generateMutation.isPending}><Sparkles className="h-4 w-4" />Generate tailored resume</Button></div> : <div className="flex min-h-0 flex-1 flex-col"><div className="min-h-0 flex-1 space-y-5 overflow-y-auto pr-1" data-testid="tailor-resume-scroll-region">
      {generateMutation.isPending && <p role="status"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Regenerating a new unsaved draft. Your current review remains available.</p>}
      {!generateMutation.isPending && generateMutation.isError && <p role="alert">Could not regenerate this draft. Your current reviewed resume is unchanged.</p>}
      {blockedForRegeneration ? <p role="alert">This resume must be re-generated after the source resume changes.</p> : null}
      {coverageAnalysis?.length ? <div className="rounded-md border bg-muted/20 p-3" data-testid="fit-coverage-analysis"><h3 className="mb-2 text-sm font-semibold">Candidate Fit and Resume Coverage</h3><CoverageGroup title="Verified and shown on this resume" items={coverageAnalysis.filter((item) => item.status === "verified" && item.resume_coverage_status === "covered")} /><CoverageGroup title="Verified, not shown on this resume" items={coverageAnalysis.filter((item) => item.status === "verified" && item.resume_coverage_status === "not_represented")} /><CoverageGroup title="Missing requirements" titleClassName="text-amber-800 dark:text-amber-200" items={coverageAnalysis.filter((item) => item.status === "missing")} /></div> : null}
      {headerPreview.length ? <div className="rounded-md border p-3" data-testid="canonical-header-preview"><h3 className="mb-2 text-sm font-semibold">Resume header</h3>{headerPreview.map((item, index) => <p key={`${item}-${index}`} className={index === 0 ? "text-sm font-semibold" : "text-sm"}>{item}</p>)}</div> : null}
       {hasCanonical ? renderedSections.map((section, sectionIndex) => { if (section.section === "contact" || section.section === "links" || section.section === "unclassified") return null; if (section.section === "work_history") return <div key="work-history">{renderWorkHistory()}</div>; if (section.section === "skills") return candidateSkills.length ? <div key={`${section.section}-${sectionIndex}`}><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{skillsHeading}</h4><div className="flex flex-wrap gap-1.5">{candidateSkills.map((skill) => <Badge key={skill}>{skill}</Badge>)}</div><EvidenceList items={reviewBase?.skill_evidence} label="Skill source and job relevance" />{skillsReordered && !readOnly && <Button className="mt-2" size="sm" variant="ghost" onClick={restoreSkillOrder}>{reviewState?.skillsSourceOrder ? "Use suggested order" : "Restore source skill order"}</Button>}</div> : null; if (section.section === "summary") return canonicalSummary.length ? <div key={`${section.section}-${sectionIndex}`} className="rounded-md border p-3" data-testid="canonical-summary"><h3 className="mb-2 text-sm font-semibold">{section.heading ?? "PROFESSIONAL SUMMARY"}</h3>{summaryComparison?.changed ? <><p className="text-xs text-muted-foreground"><b>Original:</b> {summaryComparison.original}</p><p className="text-sm"><b>Suggestion:</b> {summaryComparison.suggestion}</p><EvidenceList items={reviewBase?.summary_evidence} />{!readOnly && <Button size="sm" variant="ghost" onClick={toggleSummary}>{reviewState?.summaryRejected ? "Use suggestion" : "Keep original"}</Button>}</> : canonicalSummary.map((item, index) => <p key={index} className="text-sm">{item}</p>)}</div> : null; const matching = additionalSections.find((candidate) => candidate.section === section.section && candidate.heading === section.heading && candidate.items[0]?.source_id === section.sourceIds?.[0]); return matching ? renderAdditionalSection(matching, sectionIndex) : null; }) : <>
        {candidateSkills.length ? <div><h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{skillsHeading}</h4><div className="flex flex-wrap gap-1.5">{candidateSkills.map((skill) => <Badge key={skill}>{skill}</Badge>)}</div></div> : null}
        {!canonical.length && !data.skills?.length && data.matched_skills.length > 0 && <div><h4>Matched job requirements</h4><div className="flex flex-wrap gap-1.5">{data.matched_skills.map((skill) => <Badge key={skill}>{skill}</Badge>)}</div></div>}
        {canonicalSummary.length ? <div className="rounded-md border p-3"><h3 className="mb-2 text-sm font-semibold">Professional summary</h3>{canonicalSummary.map((item, index) => <p key={index} className="text-sm">{item}</p>)}</div> : null}
        {renderWorkHistory()}
        {additionalSections.length ? <div className="border-t pt-2"><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Additional resume sections</p>{additionalSections.map(renderAdditionalSection)}</div> : null}
      </>}
      {!readOnly && reviewState ? <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={restoreWording}><RotateCcw className="h-4 w-4" />Restore AI wording</Button><Button size="sm" variant="outline" onClick={includeAll}><RotateCcw className="h-4 w-4" />Include all experience</Button></div> : null}
      {unclassifiedSections.length ? <details className="rounded-md border p-3"><summary className="cursor-pointer text-sm font-medium">Unclassified source text ({unclassifiedSections.reduce((count, section) => count + section.items.length, 0)} items)</summary><p className="mt-2 text-xs text-muted-foreground">This source text is retained for diagnostics and excluded from your tailored resume, Copy, PDF, and DOCX exports.</p><div className="mt-2 space-y-1">{unclassifiedSections.flatMap((section) => section.items).map((item, index) => <p key={`${item.source_id ?? item.display_key}-${index}`} className="text-sm">{item.text}</p>)}</div></details> : null}
    </div><div className="mt-0 flex shrink-0 flex-wrap gap-2 border-t bg-background/95 pt-4" data-testid="tailor-resume-actions"><Button size="sm" onClick={() => void copy()} disabled={exportValidationMutation.isPending || blockedForRegeneration}><Copy className="h-4 w-4" />Copy all</Button><Button size="sm" variant="outline" onClick={() => void exportPdf()} disabled={exportValidationMutation.isPending || blockedForRegeneration}><FileDown className="h-4 w-4" />PDF</Button><Button size="sm" variant="outline" onClick={() => void exportDocx()} disabled={exportValidationMutation.isPending || blockedForRegeneration}><FileText className="h-4 w-4" />DOCX</Button>{!readOnly && <><Button size="sm" onClick={save} disabled={saveMutation.isPending}><Save className="h-4 w-4" />Save resume</Button><Button size="sm" variant="outline" onClick={generate} disabled={generateMutation.isPending}><RefreshCw className="h-4 w-4" />Regenerate draft</Button></>}</div></div>}
  </DialogContent></Dialog>;
}
