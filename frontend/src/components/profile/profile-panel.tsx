"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Award,
  Briefcase,
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
import type { EducationItem, ParsedProfile, WorkItem } from "@/lib/types";
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

const SENIORITY_OPTIONS = ["entry", "mid", "senior", "staff", "lead"];

// ---- Main component ---------------------------------------------------------

export function ProfilePanel() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const { setTask, isRunning, progressMsg } = useResumeTask();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<ParsedProfile | null>(null);

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
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No active profile yet. Upload a résumé to get started.
          </CardContent>
        </Card>
      </div>
    );
  }

  const displayProfile = editing ? form! : profile;

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
                onClick={() => editMutation.mutate(form!)}
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
        </div>
      </div>
    </div>
  );
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

// ---- Utility ----------------------------------------------------------------

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong";
}
