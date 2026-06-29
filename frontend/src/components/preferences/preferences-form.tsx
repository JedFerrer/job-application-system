"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { toast } from "sonner";
import { ApiError, preferencesApi, resumeApi } from "@/lib/api";
import type {
  PreferenceResponse,
  PreferenceUpdate,
  RemotePreference,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TagInput } from "@/components/preferences/tag-input";

const REMOTE_OPTIONS: RemotePreference[] = ["any", "remote", "hybrid", "onsite"];
const ALLOWED_SOURCES = ["indeed", "linkedin", "glassdoor", "zip_recruiter"];

interface FormState {
  jobTitles: string[];
  keywords: string[];
  excludedKeywords: string[];
  locations: string[];
  jobTypes: string[];
  enabledSources: string[];
  remotePreference: RemotePreference;
  salaryMin: string;
  resultsPerSource: number;
  hoursOld: number;
  isActive: boolean;
}

const EMPTY_FORM: FormState = {
  jobTitles: [],
  keywords: [],
  excludedKeywords: [],
  locations: [],
  jobTypes: [],
  enabledSources: ["indeed"],
  remotePreference: "any",
  salaryMin: "",
  resultsPerSource: 50,
  hoursOld: 168,
  isActive: true,
};

function fromResponse(pref: PreferenceResponse): FormState {
  return {
    jobTitles: pref.job_titles,
    keywords: pref.keywords,
    excludedKeywords: pref.excluded_keywords,
    locations: pref.locations,
    jobTypes: pref.job_types,
    enabledSources: pref.enabled_sources.length
      ? pref.enabled_sources
      : ["indeed"],
    remotePreference: pref.remote_preference,
    salaryMin: pref.salary_min === null ? "" : String(pref.salary_min),
    resultsPerSource: pref.results_per_source,
    hoursOld: pref.hours_old,
    isActive: pref.is_active,
  };
}

function toPayload(state: FormState): PreferenceUpdate {
  const salary = state.salaryMin.trim();
  return {
    job_titles: state.jobTitles,
    keywords: state.keywords,
    excluded_keywords: state.excludedKeywords,
    locations: state.locations,
    job_types: state.jobTypes,
    enabled_sources: state.enabledSources,
    remote_preference: state.remotePreference,
    salary_min: salary ? Number(salary) : null,
    results_per_source: state.resultsPerSource,
    hours_old: state.hoursOld,
    is_active: state.isActive,
  };
}

export function PreferencesForm() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [seededFromProfile, setSeededFromProfile] = useState(false);

  const prefQuery = useQuery({
    queryKey: ["preferences"],
    queryFn: preferencesApi.get,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 404) && count < 2,
  });

  // Only fetch the profile to seed defaults if no preference exists yet.
  const profileQuery = useQuery({
    queryKey: ["resume-profile"],
    queryFn: resumeApi.getProfile,
    enabled:
      prefQuery.isError &&
      prefQuery.error instanceof ApiError &&
      prefQuery.error.status === 404,
    retry: false,
  });

  // Hydrate the form from an existing preference.
  useEffect(() => {
    if (prefQuery.data) setForm(fromResponse(prefQuery.data));
  }, [prefQuery.data]);

  // First-time setup: seed job titles from the parsed résumé profile.
  useEffect(() => {
    if (seededFromProfile || !profileQuery.data) return;
    const { profile } = profileQuery.data;
    setForm((prev) => ({
      ...prev,
      jobTitles: profile.preferred_roles.length
        ? profile.preferred_roles
        : prev.jobTitles,
      keywords: profile.skills.slice(0, 10),
    }));
    setSeededFromProfile(true);
  }, [profileQuery.data, seededFromProfile]);

  const saveMutation = useMutation({
    mutationFn: (payload: PreferenceUpdate) => preferencesApi.upsert(payload),
    onSuccess: (saved) => {
      queryClient.setQueryData(["preferences"], saved);
      toast.success("Preferences saved");
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Save failed"),
  });

  const refreshMutation = useMutation({
    mutationFn: preferencesApi.refresh,
    onSuccess: () => toast.success("Scrape queued — new jobs arriving shortly"),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 429) {
        toast.error(error.message || "Please wait before refreshing again");
        return;
      }
      toast.error(error instanceof ApiError ? error.message : "Refresh failed");
    },
  });

  const canSave = useMemo(
    () => form.jobTitles.length > 0 && form.enabledSources.length > 0,
    [form.jobTitles, form.enabledSources],
  );

  function toggleSource(source: string) {
    setForm((prev) => ({
      ...prev,
      enabledSources: prev.enabledSources.includes(source)
        ? prev.enabledSources.filter((s) => s !== source)
        : [...prev.enabledSources, source],
    }));
  }

  if (prefQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading preferences…
      </div>
    );
  }

  const hasSavedPreference = Boolean(prefQuery.data);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) saveMutation.mutate(toPayload(form));
      }}
      className="flex flex-col gap-6"
    >
      <Card>
        <CardHeader>
          <CardTitle>What to search for</CardTitle>
          <CardDescription>
            Job titles drive the scheduled scrape. Keywords sharpen relevance.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-6">
          <div className="flex flex-col gap-2">
            <Label htmlFor="job-titles">Job titles</Label>
            <TagInput
              id="job-titles"
              value={form.jobTitles}
              onChange={(jobTitles) => setForm((p) => ({ ...p, jobTitles }))}
              placeholder="e.g. Backend Engineer"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="keywords">Keywords</Label>
            <TagInput
              id="keywords"
              value={form.keywords}
              onChange={(keywords) => setForm((p) => ({ ...p, keywords }))}
              placeholder="e.g. FastAPI, PostgreSQL"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="excluded">Excluded keywords</Label>
            <TagInput
              id="excluded"
              value={form.excludedKeywords}
              onChange={(excludedKeywords) =>
                setForm((p) => ({ ...p, excludedKeywords }))
              }
              placeholder="e.g. senior, clearance"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Where & how</CardTitle>
          <CardDescription>
            Locations, work mode, sources, and result volume per source.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-6">
          <div className="flex flex-col gap-2">
            <Label htmlFor="locations">Locations</Label>
            <TagInput
              id="locations"
              value={form.locations}
              onChange={(locations) => setForm((p) => ({ ...p, locations }))}
              placeholder="e.g. Philippines, Singapore"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="remote">Work mode</Label>
            <div className="flex flex-wrap gap-2">
              {REMOTE_OPTIONS.map((option) => (
                <Button
                  key={option}
                  type="button"
                  size="sm"
                  variant={
                    form.remotePreference === option ? "default" : "outline"
                  }
                  onClick={() =>
                    setForm((p) => ({ ...p, remotePreference: option }))
                  }
                  className="capitalize"
                >
                  {option}
                </Button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label>Sources</Label>
            <div className="flex flex-wrap gap-2">
              {ALLOWED_SOURCES.map((source) => (
                <Button
                  key={source}
                  type="button"
                  size="sm"
                  variant={
                    form.enabledSources.includes(source) ? "default" : "outline"
                  }
                  onClick={() => toggleSource(source)}
                  className="capitalize"
                >
                  {source.replace("_", " ")}
                </Button>
              ))}
            </div>
          </div>
          <div className="flex flex-row gap-4">
            <div className="flex flex-1 flex-col gap-2">
              <Label htmlFor="salary-min">Minimum salary (USD)</Label>
              <Input
                id="salary-min"
                inputMode="numeric"
                value={form.salaryMin}
                onChange={(event) =>
                  setForm((p) => ({
                    ...p,
                    salaryMin: event.target.value.replace(/[^\d]/g, ""),
                  }))
                }
                placeholder="optional"
              />
            </div>
            <div className="flex flex-1 flex-col gap-2">
              <Label htmlFor="results">Results per source</Label>
              <Input
                id="results"
                inputMode="numeric"
                value={String(form.resultsPerSource)}
                onChange={(event) =>
                  setForm((p) => ({
                    ...p,
                    resultsPerSource: Math.min(
                      200,
                      Math.max(1, Number(event.target.value.replace(/[^\d]/g, "")) || 1),
                    ),
                  }))
                }
              />
            </div>
            <div className="flex flex-1 flex-col gap-2">
              <Label htmlFor="hours-old">Posted within</Label>
              <select
                id="hours-old"
                value={form.hoursOld}
                onChange={(event) =>
                  setForm((p) => ({ ...p, hoursOld: Number(event.target.value) }))
                }
                className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value={24}>Last 24 hours</option>
                <option value={72}>Last 3 days</option>
                <option value={168}>Last week</option>
                <option value={336}>Last 2 weeks</option>
                <option value={504}>Last 3 weeks</option>
                <option value={720}>Last 30 days</option>
              </select>
            </div>
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-4 sm:flex-row sm:justify-between">
          <div className="flex items-center gap-3">
            <Label htmlFor="is-active" className="text-sm font-normal cursor-pointer">
              Auto-scrape new jobs
            </Label>
            <button
              id="is-active"
              type="button"
              role="switch"
              aria-checked={form.isActive}
              onClick={() =>
                setForm((p) => ({ ...p, isActive: !p.isActive }))
              }
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                form.isActive ? "bg-primary" : "bg-muted"
              }`}
            >
              <span
                className={`pointer-events-none block h-4 w-4 rounded-full bg-background shadow-sm ring-0 transition-transform ${
                  form.isActive ? "translate-x-4" : "translate-x-0"
                }`}
              />
            </button>
            <span className="text-xs text-muted-foreground">
              {form.isActive
                ? "Scheduled scraping is on"
                : "Only manual refresh will pull jobs"}
            </span>
          </div>
          <div className="flex flex-row gap-4">
            <Button type="submit" disabled={!canSave || saveMutation.isPending}>
              {saveMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
              Save preferences
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!hasSavedPreference || refreshMutation.isPending}
              onClick={() => refreshMutation.mutate()}
            >
              {refreshMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <RefreshCw className="size-4" />
              )}
              Refresh now
            </Button>
          </div>
        </CardFooter>
      </Card>
    </form>
  );
}
