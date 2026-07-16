"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, Save } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, readinessPreferencesApi } from "@/lib/api";
import type {
  ReadinessPreferenceResponse,
  ReadinessPreferenceUpdate,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

export interface ReadinessFormState {
  minimumAnnualSalary: string;
  salaryCurrency: string;
  remoteOnly: boolean;
  acceptedJobTypes: string;
  blockedKeywords: string;
}

const EMPTY_FORM: ReadinessFormState = {
  minimumAnnualSalary: "",
  salaryCurrency: "",
  remoteOnly: false,
  acceptedJobTypes: "",
  blockedKeywords: "",
};

function fromResponse(response: ReadinessPreferenceResponse): ReadinessFormState {
  return {
    minimumAnnualSalary:
      response.minimum_annual_salary === null
        ? ""
        : String(response.minimum_annual_salary),
    salaryCurrency: response.salary_currency ?? "",
    remoteOnly: response.remote_only === true,
    acceptedJobTypes: response.accepted_job_types.join(", "),
    blockedKeywords: response.blocked_keywords.join(", "),
  };
}

function splitValues(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}

function salaryError(state: ReadinessFormState): string | null {
  const salary = state.minimumAnnualSalary.trim();
  if (!salary) return null;
  const value = Number(salary);
  if (!Number.isFinite(value) || value < 0) {
    return "Minimum annual salary must be a non-negative number.";
  }
  if (!state.salaryCurrency.trim()) {
    return "Choose a currency when entering a minimum salary.";
  }
  return null;
}

export function toReadinessPayload(state: ReadinessFormState): ReadinessPreferenceUpdate {
  const salary = state.minimumAnnualSalary.trim();
  return {
    minimum_annual_salary: salary ? Number(salary) : null,
    salary_currency: salary ? state.salaryCurrency.trim().toUpperCase() : null,
    remote_only: state.remoteOnly ? true : null,
    accepted_job_types: splitValues(state.acceptedJobTypes),
    preferred_locations: [],
    blocked_keywords: splitValues(state.blockedKeywords),
  };
}

export function ReadinessPreferencesForm() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ReadinessFormState>(EMPTY_FORM);

  const preferenceQuery = useQuery({
    queryKey: ["readiness-preferences"],
    queryFn: readinessPreferencesApi.get,
  });

  useEffect(() => {
    if (preferenceQuery.data) setForm(fromResponse(preferenceQuery.data));
  }, [preferenceQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: ReadinessPreferenceUpdate) =>
      readinessPreferencesApi.upsert(payload),
    onSuccess: (saved) => {
      queryClient.setQueryData(["readiness-preferences"], saved);
      toast.success("Readiness preferences saved");
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Save failed"),
  });

  const validationError = useMemo(() => salaryError(form), [form]);

  if (preferenceQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading readiness preferences…
      </div>
    );
  }

  if (preferenceQuery.isError) {
    return (
      <Card>
        <CardContent className="flex flex-col gap-3 py-6">
          <p className="text-sm text-destructive">
            {preferenceQuery.error instanceof ApiError
              ? preferenceQuery.error.message
              : "Unable to load readiness preferences."}
          </p>
          <Button type="button" variant="outline" onClick={() => preferenceQuery.refetch()}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (!validationError) saveMutation.mutate(toReadinessPayload(form));
      }}
    >
      <Card>
        <CardHeader>
          <CardTitle>Recommendation rules</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor="minimum-annual-salary">Minimum annual salary</Label>
            <div className="flex gap-2">
              <Input
                id="minimum-annual-salary"
                type="number"
                min="0"
                step="any"
                value={form.minimumAnnualSalary}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    minimumAnnualSalary: event.target.value,
                  }))
                }
                placeholder="Optional"
                aria-describedby="salary-help salary-error"
              />
              <Input
                aria-label="Salary currency"
                value={form.salaryCurrency}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    salaryCurrency: event.target.value.toUpperCase(),
                  }))
                }
                placeholder="USD"
                maxLength={8}
                className="w-24"
              />
            </div>
            <p id="salary-help" className="text-xs text-muted-foreground">
              Salary checks are conservative and do not convert currencies.
            </p>
            {validationError && (
              <p id="salary-error" className="text-xs text-destructive" role="alert">
                {validationError}
              </p>
            )}
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.remoteOnly}
              onChange={(event) =>
                setForm((current) => ({ ...current, remoteOnly: event.target.checked }))
              }
              className="rounded border-input"
            />
            Remote-only readiness rule
          </label>

          <div className="flex flex-col gap-2">
            <Label htmlFor="accepted-job-types">Accepted job types</Label>
            <Input
              id="accepted-job-types"
              value={form.acceptedJobTypes}
              onChange={(event) =>
                setForm((current) => ({ ...current, acceptedJobTypes: event.target.value }))
              }
              placeholder="e.g. fulltime, contract"
            />
            <p className="text-xs text-muted-foreground">
              Leave empty to avoid a job-type readiness blocker.
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="blocked-keywords">Exact description blocker keywords</Label>
            <Input
              id="blocked-keywords"
              value={form.blockedKeywords}
              onChange={(event) =>
                setForm((current) => ({ ...current, blockedKeywords: event.target.value }))
              }
              placeholder="e.g. unpaid internship, commission only"
            />
            <p className="text-xs text-muted-foreground">
              Comma-separated phrases checked exactly in job descriptions. They affect
              recommendations, not search results.
            </p>
          </div>
        </CardContent>
        <CardFooter className="flex justify-end">
          <Button type="submit" disabled={Boolean(validationError) || saveMutation.isPending}>
            {saveMutation.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            Save readiness preferences
          </Button>
        </CardFooter>
      </Card>
    </form>
  );
}
