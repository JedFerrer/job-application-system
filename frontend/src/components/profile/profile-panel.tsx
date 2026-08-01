"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Award,
  Briefcase,
  ExternalLink,
  GraduationCap,
  Loader2,
  Mail,
  Minus,
  Phone,
  Plus,
  Upload,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError, resumeApi } from "@/lib/api";
import { useResumeTask } from "@/context/resume-task-context";
import type {
  EducationItem,
  ExtractionEvidence,
  ParsedProfile,
  ProfessionalLinks,
  ProjectItem,
  LanguageItem,
  ProfessionalCredential,
  ResumeImportDetail,
  ResumeImportListItem,
  WorkItem,
} from "@/lib/types";
import { validateProfileUrl } from "@/lib/validators";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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

const EMPTY_EXTRACTION_EVIDENCE: ExtractionEvidence = {
  work_history: [],
  education: [],
  certifications: [],
  projects: [],
  languages: [],
  licenses: [],
};
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const ACCEPTED = ".pdf,.docx";

// ---- Helpers ----------------------------------------------------------------

function formatDateRange(item: WorkItem | EducationItem): string {
  const start = "start" in item ? item.start : null;
  const end =
    "end" in item ? (item as WorkItem).end : (item as EducationItem).year;
  if (!start && !end) return "";
  if (!start) return end ?? "";
  if (!end) return `${start} – Present`;
  return `${start} – ${end}`;
}

function isCurrentRole(item: WorkItem): boolean {
  return (
    item.end === null || item.end === "" || item.end?.toLowerCase() === "present"
  );
}

function emptyWorkItem(): WorkItem {
  return { company: null, title: null, start: null, end: null, highlights: [] };
}

function emptyEducationItem(): EducationItem {
  return { degree: null, institution: null, year: null };
}

function emptyProjectItem(): ProjectItem {
  return {
    name: null,
    description: null,
    technologies: [],
    achievements: [],
    url: null,
  };
}

function normalizeProfile(profile: ParsedProfile): ParsedProfile {
  return {
    ...profile,
    links: Object.fromEntries(
      Object.entries(profile.links).map(([key, value]) => [key, value?.trim() || null]),
    ) as ProfessionalLinks,
    projects: profile.projects.map((project) => ({
      ...project,
      name: project.name?.trim() || null,
      description: project.description?.trim() || null,
      technologies: project.technologies.map((item) => item.trim()).filter(Boolean),
      achievements: project.achievements.map((item) => item.trim()).filter(Boolean),
      url: project.url?.trim() || null,
    })),
    languages: (profile.languages ?? []).map((item) => ({
      language: item.language.trim(),
      proficiency: item.proficiency?.trim() || null,
    })),
    licenses: (profile.licenses ?? []).map((item) => ({
      name: item.name.trim(),
      issuer: item.issuer?.trim() || null,
    })),
  };
}

const SENIORITY_OPTIONS = ["entry", "mid", "senior", "staff", "lead"];

// ---- Main component ---------------------------------------------------------

export function ProfilePanel() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const { setTask, isRunning, progressMsg } = useResumeTask();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<ParsedProfile | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  const profileQuery = useQuery({
    queryKey: ["profile"],
    queryFn: resumeApi.getProfile,
    retry: false,
  });

  const profile = profileQuery.data?.profile;

  // No active profile yet — backend returns 200 with null body (not 404).
  const noProfile = profileQuery.data === null;

  // Sync form when profile loads or changes externally.
  useEffect(() => {
    if (profile) setForm(profile);
  }, [profile]);

  const uploadMutation = useMutation({
    mutationFn: (file: File) => resumeApi.upload(file),
    onSuccess: (data, file) => setTask(data.task_id, file.name),
    onError: (err) => toast.error(errorMessage(err)),
  });

  const editMutation = useMutation({
    mutationFn: (p: ParsedProfile) => resumeApi.updateProfile(p),
    onSuccess: (response) => {
      toast.success(
        response.matching_profile_changed
          ? "Profile updated. Match rankings will update shortly."
          : "Profile updated",
      );
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      setEditing(false);
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Update failed"),
  });

  const validateForm = (value: ParsedProfile): boolean => {
    const errors: Record<string, string> = {};
    for (const [field, url] of Object.entries(value.links)) {
      const error = validateProfileUrl(url ?? "");
      if (error) errors[`link-${field}`] = error;
    }
    value.projects.forEach((project, index) => {
      const error = validateProfileUrl(project.url ?? "");
      if (error) errors[`project-${index}-url`] = error;
    });
    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleChange = (field: keyof ParsedProfile, value: unknown) => {
    setForm((prev) => (prev ? { ...prev, [field]: value } : prev));
  };

  const handlePick = () => fileRef.current?.click();
  const handleFile = (file: File | undefined) => {
    if (!file) return;
    uploadMutation.mutate(file);
  };

  if (profileQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading profile…</p>;
  }

  if (noProfile || !profile) {
    return (
      <div className="flex flex-col gap-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Upload résumé</CardTitle>
            <CardDescription>
              PDF or DOCX, up to 10 MB. The active profile is used for matching
              and cover letters.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPTED}
              className="hidden"
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
            <div className="flex items-center gap-3">
              <Button
                onClick={handlePick}
                disabled={isRunning || uploadMutation.isPending}
                size="sm"
              >
                {isRunning || uploadMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="h-4 w-4" />
                )}
                {isRunning
                  ? progressMsg ?? "Parsing…"
                  : uploadMutation.isPending
                    ? "Uploading…"
                    : "Choose file"}
              </Button>
              {uploadMutation.variables && (
                <span className="text-sm text-muted-foreground">
                  {uploadMutation.variables.name}
                </span>
              )}
            </div>
            {isRunning && (
              <p className="text-xs text-muted-foreground">
                Running in the background — you can navigate away and come back.
              </p>
            )}
          </CardContent>
        </Card>
        <ImportedResumeDrafts />
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No active profile yet. Upload a résumé to get started.
          </CardContent>
        </Card>
      </div>
    );
  }

  const displayProfile = editing ? form! : profile;
  const save = () => {
    if (form && validateForm(form)) editMutation.mutate(normalizeProfile(form));
  };

  return (
    <div className="flex flex-col gap-8">
      {/* Upload card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Upload résumé</CardTitle>
          <CardDescription>
            PDF or DOCX, up to 10 MB. The active profile is used for matching
            and cover letters.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPTED}
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <div className="flex items-center gap-3">
            <Button
              onClick={handlePick}
              disabled={isRunning || uploadMutation.isPending}
              size="sm"
            >
              {isRunning || uploadMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {isRunning
                ? progressMsg ?? "Parsing…"
                : uploadMutation.isPending
                  ? "Uploading…"
                  : "Choose file"}
            </Button>
            {uploadMutation.variables && (
              <span className="text-sm text-muted-foreground">
                {uploadMutation.variables.name}
              </span>
            )}
          </div>
          {isRunning && (
            <p className="text-xs text-muted-foreground">
              Running in the background — you can navigate away and come back.
            </p>
          )}
        </CardContent>
      </Card>

      <ImportedResumeDrafts />

      {/* Edit bar */}
      {editing ? (
        <Card>
          <CardContent className="flex items-center justify-between px-5 py-3">
            <p className="text-sm text-muted-foreground">
              Editing profile — changes will be re-embedded for better matching.
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={save}
                disabled={editMutation.isPending}
              >
                {editMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : null}
                Save all changes
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setForm(profile);
                  setValidationErrors({});
                  setEditing(false);
                }}
                disabled={editMutation.isPending}
              >
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            Review your parsed profile. Click Edit to make corrections.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setForm(profile);
              setValidationErrors({});
              setEditing(true);
            }}
          >
            Edit profile
          </Button>
        </div>
      )}

      <SummaryRow profile={displayProfile} />
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="flex flex-col gap-8 lg:col-span-1">
          <ProfileCard
            profile={displayProfile}
            editing={editing}
            onChange={handleChange}
          />
          <ProfessionalLinksSection
            links={displayProfile.links}
            editing={editing}
            errors={validationErrors}
            onChange={(links) => handleChange("links", links)}
          />
          <SkillsSection
            skills={displayProfile.skills}
            editing={editing}
            onChange={(skills) => handleChange("skills", skills)}
          />
          <CertificationsSection
            certifications={displayProfile.certifications}
            editing={editing}
            onChange={(certs) => handleChange("certifications", certs)}
          />
          <LanguagesSection
            languages={displayProfile.languages ?? []}
            editing={editing}
            onChange={(languages) => handleChange("languages", languages)}
          />
          <LicensesSection
            licenses={displayProfile.licenses ?? []}
            editing={editing}
            onChange={(licenses) => handleChange("licenses", licenses)}
          />
        </div>
        <div className="flex flex-col gap-8 lg:col-span-2">
          <WorkTimeline
            workHistory={displayProfile.work_history}
            editing={editing}
            onChange={(work_history) => handleChange("work_history", work_history)}
          />
          <EducationSection
            education={displayProfile.education}
            editing={editing}
            onChange={(education) => handleChange("education", education)}
          />
          <ProjectsSection
            projects={displayProfile.projects}
            editing={editing}
            errors={validationErrors}
            onChange={(projects) => handleChange("projects", projects)}
          />
        </div>
      </div>
    </div>
  );
}

function ImportedResumeDrafts() {
  const queryClient = useQueryClient();
  const [openImportId, setOpenImportId] = useState<number | null>(null);
  const [confirmAction, setConfirmAction] = useState<"approve" | "reject" | null>(null);
  const actionInFlightRef = useRef(false);

  const importsQuery = useQuery({
    queryKey: ["resume-imports"],
    queryFn: resumeApi.listImportDrafts,
    retry: false,
  });
  const detailQuery = useQuery({
    queryKey: ["resume-import", openImportId],
    queryFn: () => resumeApi.getImportDraft(openImportId!),
    enabled: openImportId != null,
    retry: false,
  });

  const closeReview = () => {
    setConfirmAction(null);
    setOpenImportId(null);
  };

  const approveMutation = useMutation({
    mutationFn: (resumeId: number) => resumeApi.approveImport(resumeId),
    onSuccess: (_, resumeId) => {
      actionInFlightRef.current = false;
      closeReview();
      void queryClient.invalidateQueries({ queryKey: ["profile"] });
      void queryClient.invalidateQueries({ queryKey: ["resume-imports"] });
      void queryClient.removeQueries({ queryKey: ["resume-import", resumeId] });
      toast.success("Profile updated. Match rankings will update shortly.");
    },
    onError: (err) => {
      actionInFlightRef.current = false;
      toast.error(errorMessage(err));
    },
  });
  const rejectMutation = useMutation({
    mutationFn: (resumeId: number) => resumeApi.rejectImport(resumeId),
    onSuccess: (_, resumeId) => {
      actionInFlightRef.current = false;
      closeReview();
      void queryClient.invalidateQueries({ queryKey: ["resume-imports"] });
      void queryClient.removeQueries({ queryKey: ["resume-import", resumeId] });
      toast.success("Imported resume draft rejected.");
    },
    onError: (err) => {
      actionInFlightRef.current = false;
      toast.error(errorMessage(err));
    },
  });

  const selected = importsQuery.data?.find((item) => item.resume_id === openImportId);
  const actionPending = approveMutation.isPending || rejectMutation.isPending;
  const submitAction = () => {
    if (openImportId == null || actionPending || actionInFlightRef.current) return;
    actionInFlightRef.current = true;
    if (confirmAction === "approve") approveMutation.mutate(openImportId);
    if (confirmAction === "reject") rejectMutation.mutate(openImportId);
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Imported resume drafts</CardTitle>
          <CardDescription>
            Replacement uploads wait here for review before changing your active profile.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {importsQuery.isLoading && (
            <p className="text-sm text-muted-foreground">Loading imported resume drafts…</p>
          )}
          {importsQuery.isError && (
            <p className="text-sm text-destructive">Could not load imported resume drafts.</p>
          )}
          {importsQuery.data?.length === 0 && (
            <p className="text-sm text-muted-foreground">No imported resume drafts awaiting review.</p>
          )}
          {importsQuery.data && importsQuery.data.length > 0 && (
            <div className="flex flex-col gap-2">
              {importsQuery.data.map((draft) => (
                <ImportDraftRow key={draft.resume_id} draft={draft} onOpen={() => setOpenImportId(draft.resume_id)} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={openImportId != null} onOpenChange={(open) => { if (!open && !actionPending) closeReview(); }}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>{selected?.filename ?? "Imported resume draft"}</DialogTitle>
            <DialogDescription>
              Review this extracted profile before making it your active profile. The current profile remains active until approval.
            </DialogDescription>
          </DialogHeader>
          {detailQuery.isLoading && <p className="text-sm text-muted-foreground">Loading draft profile…</p>}
          {detailQuery.isError && <p className="text-sm text-destructive">Could not load this imported resume draft.</p>}
          {detailQuery.data && (
            <>
              <DraftProfileView
                profile={detailQuery.data.profile}
                evidence={detailQuery.data.extraction_evidence ?? EMPTY_EXTRACTION_EVIDENCE}
              />
              <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <Button variant="outline" onClick={closeReview} disabled={actionPending}>Cancel</Button>
                <Button variant="outline" onClick={() => setConfirmAction("reject")} disabled={actionPending}>Reject draft</Button>
                <Button onClick={() => setConfirmAction("approve")} disabled={actionPending}>Approve draft</Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmAction != null} onOpenChange={(open) => { if (!open && !actionPending) setConfirmAction(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmAction === "approve" ? "Approve imported profile?" : "Reject imported profile?"}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction === "approve"
                ? "This will make the imported profile active and replace the current matching profile."
                : "This will remove the imported draft from your pending reviews. Your active profile will not change."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={actionPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction disabled={actionPending} onClick={(event) => { event.preventDefault(); submitAction(); }}>
              {actionPending ? "Saving…" : confirmAction === "approve" ? "Approve draft" : "Reject draft"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function ImportDraftRow({ draft, onOpen }: { draft: ResumeImportListItem; onOpen: () => void }) {
  return (
    <div className="flex flex-col gap-3 rounded-md border p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{draft.filename}</p>
        <p className="text-xs text-muted-foreground">
          Imported {formatImportDate(draft.created_at)} · {draft.lifecycle_status}
        </p>
      </div>
      <Button size="sm" variant="outline" onClick={onOpen}>Review draft</Button>
    </div>
  );
}

function DraftProfileView({
  profile,
  evidence,
}: {
  profile: ResumeImportDetail["profile"];
  evidence: ExtractionEvidence;
}) {
  const links = profile.links ?? {};
  const projects = profile.projects ?? [];
  const workHistory = profile.work_history ?? [];
  const skills = profile.skills ?? [];
  const education = profile.education ?? [];
  const certifications = profile.certifications ?? [];
  const languages = profile.languages ?? [];
  const licenses = profile.licenses ?? [];
  return (
    <div className="flex max-h-[55vh] flex-col gap-5 overflow-y-auto text-sm">
      <div>
        <h3 className="text-lg font-semibold">{profile.full_name ?? "Candidate profile"}</h3>
        {profile.headline && <p className="text-muted-foreground">{profile.headline}</p>}
        <p className="mt-1 text-muted-foreground">{[profile.email, profile.phone].filter(Boolean).join(" · ") || "No contact details"}</p>
      </div>
      {skills.length > 0 && <ReviewSection title="Skills"><div className="flex flex-wrap gap-2">{skills.map((skill) => <Badge key={skill} variant="secondary">{skill}</Badge>)}</div></ReviewSection>}
      {workHistory.length > 0 && <ReviewSection title="Work history"><ul className="list-disc space-y-1 pl-5">{workHistory.map((item, index) => <li key={`${item.company ?? "role"}-${index}`}>{[item.title, item.company].filter(Boolean).join(" · ") || "Role"}{item.highlights.length > 0 && ` — ${item.highlights.join("; ")}`}</li>)}</ul></ReviewSection>}
      {education.length > 0 && <ReviewSection title="Education"><ul className="list-disc space-y-1 pl-5">{education.map((item, index) => <li key={index}>{[item.degree, item.institution, item.year].filter(Boolean).join(" · ") || "Education"}</li>)}</ul></ReviewSection>}
      {certifications.length > 0 && <ReviewSection title="Certifications"><p>{certifications.join(", ")}</p></ReviewSection>}
      <ReviewSection title="Languages">
        {languages.length > 0 ? <ul className="list-disc space-y-1 pl-5">{languages.map((item, index) => <li key={`${item.language}-${index}`}>{item.language}{item.proficiency ? ` · ${item.proficiency}` : ""}</li>)}</ul> : <p className="text-muted-foreground">No languages added.</p>}
      </ReviewSection>
      <ReviewSection title="Licenses and registrations">
        {licenses.length > 0 ? <ul className="list-disc space-y-1 pl-5">{licenses.map((item, index) => <li key={`${item.name}-${item.issuer ?? ""}-${index}`}>{item.name}{item.issuer ? ` · ${item.issuer}` : ""}</li>)}</ul> : <p className="text-muted-foreground">No licenses or registrations added.</p>}
      </ReviewSection>
      <ReviewSection title="Professional links">
        {Object.entries(links).some(([, value]) => value) ? <div className="flex flex-wrap gap-3">{Object.entries(links).map(([name, value]) => value && <a key={name} className="text-primary underline" href={value} target="_blank" rel="noopener noreferrer">{name}</a>)}</div> : <p className="text-muted-foreground">No professional links added.</p>}
      </ReviewSection>
      <ReviewSection title="Projects">
        {projects.length > 0 ? <div className="flex flex-col gap-3">{projects.map((project, index) => <div key={index} className="rounded-md border p-3"><p className="font-medium">{project.name ?? "Project"}</p>{project.description && <p className="mt-1 text-muted-foreground">{project.description}</p>}{project.technologies.length > 0 && <p className="mt-1 text-xs text-muted-foreground">Technologies: {project.technologies.join(", ")}</p>}{project.achievements.length > 0 && <p className="mt-1 text-xs text-muted-foreground">{project.achievements.join("; ")}</p>}</div>)}</div> : <p className="text-muted-foreground">No projects added.</p>}
      </ReviewSection>
      <EvidenceReview evidence={evidence} />
    </div>
  );
}

function EvidenceReview({ evidence }: { evidence: ExtractionEvidence }) {
  const groups = [
    ["Work history", evidence.work_history],
    ["Education", evidence.education],
    ["Certifications", evidence.certifications],
    ["Projects", evidence.projects],
    ["Languages", evidence.languages ?? []],
    ["Licenses and registrations", evidence.licenses ?? []],
  ] as const;
  const available = groups.some(([, items]) => items.length > 0);
  if (!available) return null;
  return (
    <ReviewSection title="Source evidence">
      <div className="flex flex-col gap-2">
        {groups.flatMap(([label, items]) =>
          items.map((item) => (
            <details key={`${label}-${item.item_index}`} className="rounded border p-2 text-xs">
              <summary className="cursor-pointer select-none text-primary">
                Source excerpt · {label} item {item.item_index + 1}
              </summary>
              <p className="mt-2 border-l-2 pl-2 text-muted-foreground">{item.excerpt}</p>
            </details>
          )),
        )}
      </div>
    </ReviewSection>
  );
}

function ReviewSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h4 className="mb-2 font-semibold">{title}</h4>{children}</section>;
}

function formatImportDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "unknown date" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

// ---- Summary row ------------------------------------------------------------

function SummaryRow({ profile }: { profile: ParsedProfile }) {
  const metrics = [
    {
      label: "Years experience",
      value:
        profile.years_experience != null
          ? String(profile.years_experience)
          : "—",
      icon: Zap,
      color: "text-amber-500",
    },
    {
      label: "Skills",
      value: String(profile.skills.length),
      icon: Award,
      color: "text-purple-500",
    },
    {
      label: "Roles",
      value: String(profile.work_history.length),
      icon: Briefcase,
      color: "text-blue-500",
    },
    {
      label: "Certifications",
      value: String(profile.certifications.length),
      icon: GraduationCap,
      color: "text-green-500",
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {metrics.map((m) => (
        <Card key={m.label}>
          <CardContent className="flex items-center gap-3 px-5 py-4">
            <m.icon className={`h-5 w-5 shrink-0 ${m.color}`} />
            <div className="min-w-0">
              <p className="text-2xl font-semibold leading-tight">{m.value}</p>
              <p className="truncate text-xs text-muted-foreground">
                {m.label}
              </p>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---- Profile card (identity only) -------------------------------------------

function ProfileCard({
  profile,
  editing,
  onChange,
}: {
  profile: ParsedProfile;
  editing: boolean;
  onChange: (field: keyof ParsedProfile, value: unknown) => void;
}) {
  if (editing) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Identity</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="pf-name">Full name</Label>
            <Input
              id="pf-name"
              value={profile.full_name ?? ""}
              onChange={(e) =>
                onChange("full_name", e.target.value || null)
              }
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="pf-headline">Headline</Label>
            <Input
              id="pf-headline"
              value={profile.headline ?? ""}
              onChange={(e) =>
                onChange("headline", e.target.value || null)
              }
              placeholder="e.g. Senior Frontend Engineer"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="pf-email">Email</Label>
            <Input
              id="pf-email"
              type="email"
              value={profile.email ?? ""}
              onChange={(e) =>
                onChange("email", e.target.value || null)
              }
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="pf-phone">Phone</Label>
            <Input
              id="pf-phone"
              value={profile.phone ?? ""}
              onChange={(e) =>
                onChange("phone", e.target.value || null)
              }
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="pf-seniority">Seniority</Label>
            <select
              id="pf-seniority"
              value={profile.seniority ?? ""}
              onChange={(e) =>
                onChange("seniority", e.target.value || null)
              }
              className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">—</option>
              {SENIORITY_OPTIONS.map((s) => (
                <option key={s} value={s} className="capitalize">
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="pf-years">Years experience</Label>
            <Input
              id="pf-years"
              type="number"
              min={0}
              step={0.5}
              value={profile.years_experience ?? ""}
              onChange={(e) =>
                onChange(
                  "years_experience",
                  e.target.value ? parseFloat(e.target.value) : null,
                )
              }
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="pf-roles">Preferred roles (comma-separated)</Label>
            <Textarea
              id="pf-roles"
              value={profile.preferred_roles.join(", ")}
              onChange={(e) =>
                onChange(
                  "preferred_roles",
                  e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                )
              }
              rows={2}
              placeholder="e.g. Frontend Engineer, Full Stack Developer"
            />
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">
          {profile.full_name ?? "Candidate profile"}
        </CardTitle>
        {profile.headline && (
          <CardDescription>{profile.headline}</CardDescription>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {profile.seniority && (
          <Badge variant="secondary" className="w-fit capitalize">
            {profile.seniority}
          </Badge>
        )}
        <div className="flex flex-col gap-2 text-sm text-muted-foreground">
          {profile.email && (
            <span className="flex items-center gap-2">
              <Mail className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{profile.email}</span>
            </span>
          )}
          {profile.phone && (
            <span className="flex items-center gap-2">
              <Phone className="h-3.5 w-3.5 shrink-0" />
              {profile.phone}
            </span>
          )}
          {profile.preferred_roles.length > 0 && (
            <span className="flex items-start gap-2">
              <Briefcase className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{profile.preferred_roles.join(", ")}</span>
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---- Professional links (editable) ------------------------------------------

const LINK_FIELDS: Array<[keyof ProfessionalLinks, string]> = [
  ["linkedin", "LinkedIn"],
  ["github", "GitHub"],
  ["portfolio", "Portfolio"],
  ["website", "Website"],
];

function ProfessionalLinksSection({
  links,
  editing,
  errors,
  onChange,
}: {
  links: ProfessionalLinks;
  editing: boolean;
  errors: Record<string, string>;
  onChange: (links: ProfessionalLinks) => void;
}) {
  const populated = LINK_FIELDS.filter(([field]) => links?.[field]);
  if (!editing && populated.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">Professional links</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">No professional links added.</p></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader><CardTitle className="text-base">Professional links</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3">
        {editing
          ? LINK_FIELDS.map(([field, label]) => {
              const error = errors[`link-${field}`];
              const errorId = `link-${field}-error`;
              return (
                <div key={field} className="flex flex-col gap-1.5">
                  <Label htmlFor={`pf-link-${field}`}>{label}</Label>
                  <Input
                    id={`pf-link-${field}`}
                    value={links[field] ?? ""}
                    onChange={(event) =>
                      onChange({ ...links, [field]: event.target.value || null })
                    }
                    placeholder="https://example.com"
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? errorId : undefined}
                  />
                  {error && <p id={errorId} role="alert" className="text-xs text-destructive">{error}</p>}
                </div>
              );
            })
          : populated.map(([field, label]) => (
              <a
                key={field}
                href={links[field] ?? undefined}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm text-primary underline-offset-4 hover:underline"
              >
                <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                {label}
              </a>
            ))}
      </CardContent>
    </Card>
  );
}

// ---- Projects (editable) -----------------------------------------------------

function ProjectsSection({
  projects,
  editing,
  errors,
  onChange,
}: {
  projects: ProjectItem[];
  editing: boolean;
  errors: Record<string, string>;
  onChange: (projects: ProjectItem[]) => void;
}) {
  if (!editing && projects.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">Projects</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">No projects added.</p></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Projects</CardTitle>
        {editing && (
          <Button size="sm" variant="outline" onClick={() => onChange([...projects, emptyProjectItem()])}>
            <Plus className="h-4 w-4" />
            Add project
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {projects.map((project, index) => {
          const set = (patch: Partial<ProjectItem>) => {
            const next = [...projects];
            const current = projects[index];
            if (!current) return;
            next[index] = { ...current, ...patch };
            onChange(next);
          };
          const urlError = errors[`project-${index}-url`];
          const errorId = `project-${index}-url-error`;
          if (!editing) {
            return (
              <div key={index} className="rounded-md border p-4">
                <p className="font-medium">{project.name ?? "Project"}</p>
                {project.description && <p className="mt-1 text-sm text-muted-foreground">{project.description}</p>}
                {project.technologies.length > 0 && <p className="mt-2 text-xs text-muted-foreground"><span className="font-medium text-foreground">Technologies:</span> {project.technologies.join(", ")}</p>}
                {project.achievements.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">{project.achievements.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>}
                {project.url && <a href={project.url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-2 text-sm text-primary underline-offset-4 hover:underline"><ExternalLink className="h-3.5 w-3.5" />Project link</a>}
              </div>
            );
          }
          return (
            <div key={index} className="relative rounded-md border p-4">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Remove project ${index + 1}`}
                className="absolute right-2 top-2 h-7 px-2 text-muted-foreground hover:text-destructive"
                onClick={() => onChange(projects.filter((_, itemIndex) => itemIndex !== index))}
              >
                <Minus className="h-3.5 w-3.5" />
                <span className="sr-only">Remove</span>
              </Button>
              <div className="flex flex-col gap-3 pr-12">
                <div className="flex flex-col gap-1.5"><Label htmlFor={`pf-project-${index}-name`}>Name</Label><Input id={`pf-project-${index}-name`} value={project.name ?? ""} onChange={(event) => set({ name: event.target.value || null })} /></div>
                <div className="flex flex-col gap-1.5"><Label htmlFor={`pf-project-${index}-description`}>Description</Label><Textarea id={`pf-project-${index}-description`} value={project.description ?? ""} onChange={(event) => set({ description: event.target.value || null })} rows={2} /></div>
                <div className="flex flex-col gap-1.5"><Label htmlFor={`pf-project-${index}-url`}>URL</Label><Input id={`pf-project-${index}-url`} value={project.url ?? ""} onChange={(event) => set({ url: event.target.value || null })} placeholder="https://example.com" aria-invalid={urlError ? true : undefined} aria-describedby={urlError ? errorId : undefined} />{urlError && <p id={errorId} role="alert" className="text-xs text-destructive">{urlError}</p>}</div>
                <div className="flex flex-col gap-1.5"><Label htmlFor={`pf-project-${index}-technologies`}>Technologies (one per line)</Label><Textarea id={`pf-project-${index}-technologies`} value={project.technologies.join("\n")} onChange={(event) => set({ technologies: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} rows={2} /></div>
                <div className="flex flex-col gap-1.5"><Label htmlFor={`pf-project-${index}-achievements`}>Achievements (one per line)</Label><Textarea id={`pf-project-${index}-achievements`} value={project.achievements.join("\n")} onChange={(event) => set({ achievements: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} rows={2} /></div>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

// ---- Work timeline (editable) -----------------------------------------------

function WorkTimeline({
  workHistory,
  editing,
  onChange,
}: {
  workHistory: WorkItem[];
  editing: boolean;
  onChange: (items: WorkItem[]) => void;
}) {
  if (!editing && workHistory.length === 0) return null;

  if (editing) {
    return (
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Briefcase className="h-4 w-4 text-blue-500" />
            Work experience
          </CardTitle>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onChange([...workHistory, emptyWorkItem()])}
          >
            <Plus className="h-4 w-4" />
            Add role
          </Button>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-4">
            {workHistory.map((item, i) => {
              const set = (patch: Partial<WorkItem>) => {
                const next = [...workHistory];
                next[i] = { ...next[i], ...patch } as WorkItem;
                onChange(next);
              };
              return (
                <div
                  key={i}
                  className="relative rounded-md border p-4"
                >
                  <Button
                    size="sm"
                    variant="ghost"
                    className="absolute right-2 top-2 h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                    onClick={() =>
                      onChange(workHistory.filter((_, j) => j !== i))
                    }
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </Button>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs">Title</Label>
                      <Input
                        value={item.title ?? ""}
                        onChange={(e) => set({ title: e.target.value || null })}
                        placeholder="e.g. Software Engineer"
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs">Company</Label>
                      <Input
                        value={item.company ?? ""}
                        onChange={(e) =>
                          set({ company: e.target.value || null })
                        }
                        placeholder="e.g. Acme Corp"
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs">Start date</Label>
                      <Input
                        value={item.start ?? ""}
                        onChange={(e) =>
                          set({ start: e.target.value || null })
                        }
                        placeholder="e.g. Jan 2022"
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs">End date</Label>
                      <Input
                        value={item.end ?? ""}
                        onChange={(e) =>
                          set({ end: e.target.value || null })
                        }
                        placeholder="Present or e.g. Dec 2023"
                        className="h-8 text-sm"
                      />
                    </div>
                  </div>
                  <div className="mt-3 flex flex-col gap-1.5">
                    <Label className="text-xs">
                      Highlights (one per line)
                    </Label>
                    <Textarea
                      value={item.highlights.join("\n")}
                      onChange={(e) =>
                        set({
                          highlights: e.target.value
                            .split("\n")
                            .map((s) => s.trim())
                            .filter(Boolean),
                        })
                      }
                      rows={3}
                      className="text-sm"
                      placeholder="e.g. Led migration to React 18&#10;Reduced bundle size by 40%&#10;Mentored 3 junior engineers"
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Briefcase className="h-4 w-4 text-blue-500" />
          Work experience
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="relative">
          <div className="absolute bottom-1 left-[7px] top-1 w-px bg-border" />
          <div className="flex flex-col gap-4">
            {workHistory.map((item, i) => (
              <div
                key={`${item.company}-${i}`}
                className="relative flex gap-4 pl-6"
              >
                <div
                  className={`absolute left-0 top-1.5 h-[15px] w-[15px] rounded-full border-2 ${
                    isCurrentRole(item)
                      ? "border-primary bg-primary"
                      : "border-muted-foreground/30 bg-background"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium">
                        {item.title ?? "Role"}
                      </p>
                      {item.company && (
                        <p className="text-sm text-muted-foreground">
                          {item.company}
                        </p>
                      )}
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {formatDateRange(item)}
                    </span>
                  </div>
                  {isCurrentRole(item) && (
                    <Badge variant="secondary" className="mt-1 text-[10px]">
                      Current
                    </Badge>
                  )}
                  {item.highlights.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {item.highlights.map((h, j) => (
                        <li
                          key={j}
                          className="text-xs leading-relaxed text-muted-foreground"
                        >
                          {h}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---- Skills section (editable) ----------------------------------------------

function SkillsSection({
  skills,
  editing,
  onChange,
}: {
  skills: string[];
  editing: boolean;
  onChange: (skills: string[]) => void;
}) {
  const [newSkill, setNewSkill] = useState("");

  const addSkill = () => {
    const s = newSkill.trim();
    if (s && !skills.includes(s)) {
      onChange([...skills, s]);
      setNewSkill("");
    }
  };

  if (!editing && skills.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Zap className="h-4 w-4 text-amber-500" />
          Skills &amp; technologies
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {skills.map((s) => (
            <Badge
              key={s}
              variant="secondary"
              className={editing ? "gap-1 pr-1.5" : ""}
            >
              {s}
              {editing && (
                <button
                  type="button"
                  onClick={() => onChange(skills.filter((x) => x !== s))}
                  className="ml-0.5 rounded-full p-0.5 hover:bg-muted-foreground/20"
                >
                  <Minus className="h-3 w-3" />
                </button>
              )}
            </Badge>
          ))}
        </div>
        {editing && (
          <div className="mt-3 flex gap-2">
            <Input
              value={newSkill}
              onChange={(e) => setNewSkill(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addSkill())}
              placeholder="Add a skill…"
              className="h-8 max-w-xs text-sm"
            />
            <Button size="sm" variant="outline" onClick={addSkill}>
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---- Education section (editable) -------------------------------------------

function EducationSection({
  education,
  editing,
  onChange,
}: {
  education: EducationItem[];
  editing: boolean;
  onChange: (items: EducationItem[]) => void;
}) {
  if (!editing && education.length === 0) return null;

  if (editing) {
    return (
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <GraduationCap className="h-4 w-4 text-green-500" />
            Education
          </CardTitle>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onChange([...education, emptyEducationItem()])}
          >
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-4">
            {education.map((item, i) => {
              const set = (patch: Partial<EducationItem>) => {
                const next = [...education];
                next[i] = { ...next[i], ...patch } as EducationItem;
                onChange(next);
              };
              return (
                <div
                  key={i}
                  className="relative rounded-md border p-4"
                >
                  <Button
                    size="sm"
                    variant="ghost"
                    className="absolute right-2 top-2 h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                    onClick={() =>
                      onChange(education.filter((_, j) => j !== i))
                    }
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </Button>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs">Degree</Label>
                      <Input
                        value={item.degree ?? ""}
                        onChange={(e) =>
                          set({ degree: e.target.value || null })
                        }
                        placeholder="e.g. B.S. Computer Science"
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs">Institution</Label>
                      <Input
                        value={item.institution ?? ""}
                        onChange={(e) =>
                          set({ institution: e.target.value || null })
                        }
                        placeholder="e.g. MIT"
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs">Year</Label>
                      <Input
                        value={item.year ?? ""}
                        onChange={(e) =>
                          set({ year: e.target.value || null })
                        }
                        placeholder="e.g. 2019"
                        className="h-8 text-sm"
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <GraduationCap className="h-4 w-4 text-green-500" />
          Education
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-4">
          {education.map((item, i) => (
            <div
              key={i}
              className="flex items-start justify-between gap-3"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  {item.degree ?? "Degree"}
                </p>
                {item.institution && (
                  <p className="text-sm text-muted-foreground">
                    {item.institution}
                  </p>
                )}
              </div>
              {item.year && (
                <span className="shrink-0 text-xs text-muted-foreground">
                  {item.year}
                </span>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---- Certifications section (editable) --------------------------------------

function CertificationsSection({
  certifications,
  editing,
  onChange,
}: {
  certifications: string[];
  editing: boolean;
  onChange: (certs: string[]) => void;
}) {
  const [newCert, setNewCert] = useState("");

  const addCert = () => {
    const c = newCert.trim();
    if (c && !certifications.includes(c)) {
      onChange([...certifications, c]);
      setNewCert("");
    }
  };

  if (!editing && certifications.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Award className="h-4 w-4 text-purple-500" />
          Certifications
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {certifications.map((c) => (
            <Badge
              key={c}
              variant="outline"
              className={editing ? "gap-1 pr-1.5 text-sm px-3 py-1" : "text-sm px-3 py-1"}
            >
              {c}
              {editing && (
                <button
                  type="button"
                  onClick={() =>
                    onChange(certifications.filter((x) => x !== c))
                  }
                  className="ml-0.5 rounded-full p-0.5 hover:bg-muted-foreground/20"
                >
                  <Minus className="h-3 w-3" />
                </button>
              )}
            </Badge>
          ))}
        </div>
        {editing && (
          <div className="mt-3 flex gap-2">
            <Input
              value={newCert}
              onChange={(e) => setNewCert(e.target.value)}
              onKeyDown={(e) =>
                e.key === "Enter" && (e.preventDefault(), addCert())
              }
              placeholder="Add a certification…"
              className="h-8 max-w-xs text-sm"
            />
            <Button size="sm" variant="outline" onClick={addCert}>
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LanguagesSection({
  languages,
  editing,
  onChange,
}: {
  languages: LanguageItem[];
  editing: boolean;
  onChange: (languages: LanguageItem[]) => void;
}) {
  if (!editing && languages.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">Languages</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">No languages added.</p></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Languages</CardTitle>
        {editing && (
          <Button
            size="sm"
            variant="outline"
            disabled={languages.length >= 20}
            onClick={() => onChange([...languages, { language: "", proficiency: null }])}
          >
            <Plus className="h-4 w-4" />
            Add language
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {languages.map((item, index) => {
          const set = (patch: Partial<LanguageItem>) => {
            const next = [...languages];
            next[index] = { ...item, ...patch };
            onChange(next);
          };
          if (!editing) {
            return (
              <div key={`${item.language}-${index}`} className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
                <span className="font-medium">{item.language}</span>
                {item.proficiency && <span className="text-sm text-muted-foreground">{item.proficiency}</span>}
              </div>
            );
          }
          return (
            <div key={index} className="relative grid grid-cols-1 gap-3 rounded-md border p-4 sm:grid-cols-2 sm:pr-12">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`pf-language-${index}`}>Language</Label>
                <Input
                  id={`pf-language-${index}`}
                  value={item.language}
                  onChange={(event) => set({ language: event.target.value })}
                  maxLength={100}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`pf-language-proficiency-${index}`}>Proficiency</Label>
                <Input
                  id={`pf-language-proficiency-${index}`}
                  value={item.proficiency ?? ""}
                  onChange={(event) => set({ proficiency: event.target.value || null })}
                  maxLength={100}
                  placeholder="Optional"
                />
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Remove ${item.language.trim() || `language ${index + 1}`}`}
                className="absolute right-2 top-2 h-7 px-2 text-muted-foreground hover:text-destructive"
                onClick={() => onChange(languages.filter((_, itemIndex) => itemIndex !== index))}
              >
                <Minus className="h-3.5 w-3.5" />
                <span className="sr-only">Remove</span>
              </Button>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function LicensesSection({
  licenses,
  editing,
  onChange,
}: {
  licenses: ProfessionalCredential[];
  editing: boolean;
  onChange: (licenses: ProfessionalCredential[]) => void;
}) {
  if (!editing && licenses.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">Licenses and registrations</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">No licenses or registrations added.</p></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Licenses and registrations</CardTitle>
        {editing && (
          <Button
            size="sm"
            variant="outline"
            disabled={licenses.length >= 30}
            onClick={() => onChange([...licenses, { name: "", issuer: null }])}
          >
            <Plus className="h-4 w-4" />
            Add credential
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {licenses.map((item, index) => {
          const set = (patch: Partial<ProfessionalCredential>) => {
            const next = [...licenses];
            next[index] = { ...item, ...patch };
            onChange(next);
          };
          if (!editing) {
            return (
              <div key={`${item.name}-${item.issuer ?? ""}-${index}`} className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
                <span className="font-medium">{item.name}</span>
                {item.issuer && <span className="text-sm text-muted-foreground">{item.issuer}</span>}
              </div>
            );
          }
          return (
            <div key={index} className="relative grid grid-cols-1 gap-3 rounded-md border p-4 sm:grid-cols-2 sm:pr-12">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`pf-license-${index}`}>Credential</Label>
                <Input
                  id={`pf-license-${index}`}
                  value={item.name}
                  onChange={(event) => set({ name: event.target.value })}
                  maxLength={255}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`pf-license-issuer-${index}`}>Issuer</Label>
                <Input
                  id={`pf-license-issuer-${index}`}
                  value={item.issuer ?? ""}
                  onChange={(event) => set({ issuer: event.target.value || null })}
                  maxLength={255}
                  placeholder="Optional"
                />
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Remove ${item.name.trim() || `credential ${index + 1}`}`}
                className="absolute right-2 top-2 h-7 px-2 text-muted-foreground hover:text-destructive"
                onClick={() => onChange(licenses.filter((_, itemIndex) => itemIndex !== index))}
              >
                <Minus className="h-3.5 w-3.5" />
                <span className="sr-only">Remove</span>
              </Button>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

// ---- Utility ----------------------------------------------------------------

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong";
}
