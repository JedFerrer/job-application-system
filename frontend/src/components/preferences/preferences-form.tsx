"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Save } from "lucide-react";
import { toast } from "sonner";
import { ApiError, preferencesApi, resumeApi } from "@/lib/api";
import type { PreferenceResponse, PreferenceUpdate } from "@/lib/types";
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
import type { RemotePreference } from "@/lib/types";

interface FormState {
  jobTitles: string;
  locations: string;
  remotePreference: RemotePreference;
}

const EMPTY_FORM: FormState = {
  jobTitles: "",
  locations: "",
  remotePreference: "any",
};

function fromResponse(pref: PreferenceResponse): FormState {
  return {
    jobTitles: pref.job_titles.join(", "),
    locations: pref.locations.join(", "),
    remotePreference: pref.remote_preference ?? "any",
  };
}

function toPayload(state: FormState): PreferenceUpdate {
  return {
    job_titles: state.jobTitles
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    locations: state.locations
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    enabled_sources: ["indeed", "linkedin"],
    remote_preference: state.remotePreference,
    results_per_source: 50,
    hours_old: 24,
    is_active: true,
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
      jobTitles: profile.preferred_roles.join(", "),
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
    () => form.jobTitles.trim().length > 0,
    [form.jobTitles],
  );

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
            Enter comma-separated job titles and locations. The scraper runs daily
            on Indeed & LinkedIn for the last 24 hours of postings.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-row gap-4">
            <div className="flex flex-1 flex-col gap-2">
              <Label htmlFor="job-titles">Job title</Label>
              <Input
                id="job-titles"
                value={form.jobTitles}
                onChange={(event) =>
                  setForm((p) => ({ ...p, jobTitles: event.target.value }))
                }
                placeholder="e.g. Backend Engineer, Frontend Developer"
              />
            </div>
            <div className="flex flex-1 flex-col gap-2">
              <Label htmlFor="locations">Locations</Label>
              <Input
                id="locations"
                value={form.locations}
                onChange={(event) =>
                  setForm((p) => ({ ...p, locations: event.target.value }))
                }
                placeholder="e.g. Philippines, Singapore, Remote"
              />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="remote-preference">Remote preference</Label>
            <select
              id="remote-preference"
              value={form.remotePreference}
              onChange={(event) =>
                setForm((p) => ({
                  ...p,
                  remotePreference: event.target.value as RemotePreference,
                }))
              }
              className="flex h-10 w-[200px] rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="any">All (no filter)</option>
              <option value="remote">Remote only</option>
              <option value="onsite">On-site only</option>
            </select>
          </div>
        </CardContent>
        <CardFooter className="flex flex-row justify-end gap-4">
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
        </CardFooter>
      </Card>
    </form>
  );
}
