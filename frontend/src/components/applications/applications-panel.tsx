"use client";

import { useState, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, X } from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi } from "@/lib/api";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";
import { Button } from "@/components/ui/button";
import {
  APPLICATION_STATUSES,
  type ApplicationListItem,
  type ApplicationListResponse,
  type ApplicationStatus,
} from "@/lib/types";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export function ApplicationsPanel() {
  const queryClient = useQueryClient();
  const [coverLetterJob, setCoverLetterJob] = useState<{
    jobId: number;
    jobTitle: string;
  } | null>(null);
  const [filterStatus, setFilterStatus] = useState<ApplicationStatus | null>(null);

  const appsQuery = useQuery({
    queryKey: ["applications"],
    queryFn: applicationsApi.list,
  });

  const { filteredItems, statusCounts } = useMemo(() => {
    const items = appsQuery.data?.items ?? [];
    const counts: Record<ApplicationStatus, number> = {
      saved: 0,
      applied: 0,
      interview: 0,
      offer: 0,
      rejected: 0,
      closed: 0,
    };
    for (const item of items) {
      counts[item.status] = (counts[item.status] ?? 0) + 1;
    }
    return {
      filteredItems: filterStatus
        ? items.filter((a) => a.status === filterStatus)
        : items,
      statusCounts: counts,
    };
  }, [appsQuery.data, filterStatus]);

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: ApplicationStatus }) =>
      applicationsApi.update(id, { status }),
    onMutate: async ({ id, status }) => {
      await queryClient.cancelQueries({ queryKey: ["applications"] });
      const prev = queryClient.getQueryData<ApplicationListResponse>([
        "applications",
      ]);
      queryClient.setQueryData<ApplicationListResponse>(
        ["applications"],
        (old) =>
          old
            ? {
                ...old,
                items: old.items.map((a) =>
                  a.id === id ? { ...a, status } : a,
                ),
              }
            : old,
      );
      return { prev };
    },
    onError: (err, _vars, context) => {
      if (context?.prev) {
        queryClient.setQueryData(["applications"], context.prev);
      }
      toast.error(err instanceof ApiError ? err.message : "Update failed");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  if (appsQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading applications…</p>;
  }

  if (appsQuery.data && appsQuery.data.items.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          No applications yet. Save a match from the Matches page.
        </CardContent>
      </Card>
    );
  }

  const STATUS_BADGE_VARIANT: Record<ApplicationStatus, "default" | "secondary" | "outline" | "destructive"> = {
    saved: "secondary",
    applied: "default",
    interview: "default",
    offer: "default",
    rejected: "destructive",
    closed: "outline",
  };

  return (
    <>
      {/* Status filter bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={filterStatus === null ? "default" : "outline"}
          className="cursor-pointer"
          onClick={() => setFilterStatus(null)}
        >
          All ({appsQuery.data?.total ?? 0})
        </Badge>
        {APPLICATION_STATUSES.map((status) => (
          <Badge
            key={status}
            variant={
              filterStatus === status
                ? STATUS_BADGE_VARIANT[status]
                : "outline"
            }
            className="cursor-pointer capitalize"
            onClick={() =>
              setFilterStatus(filterStatus === status ? null : status)
            }
          >
            {status} ({statusCounts[status]})
          </Badge>
        ))}
        {filterStatus && (
          <button
            type="button"
            onClick={() => setFilterStatus(null)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" />
            Clear filter
          </button>
        )}
      </div>

      {filteredItems.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No applications match the selected filter.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="px-4 py-3 font-medium">Job</th>
                    <th className="px-4 py-3 font-medium">Company</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Cover letter</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((app) => (
                    <ApplicationRow
                      key={app.id}
                      app={app}
                      onStatusChange={(status) =>
                        statusMutation.mutate({ id: app.id, status })
                      }
                      onCoverLetterClick={() =>
                        setCoverLetterJob({
                          jobId: app.job_id,
                          jobTitle: app.job_title,
                        })
                      }
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      <CoverLetterDialog
        jobId={coverLetterJob?.jobId ?? null}
        jobTitle={coverLetterJob?.jobTitle}
        open={coverLetterJob != null}
        onOpenChange={(open) => {
          if (!open) setCoverLetterJob(null);
        }}
      />
    </>
  );
}

function ApplicationRow({
  app,
  onStatusChange,
  onCoverLetterClick,
}: {
  app: ApplicationListItem;
  onStatusChange: (status: ApplicationStatus) => void;
  onCoverLetterClick: () => void;
}) {
  return (
    <tr className="border-b last:border-0">
      <td className="px-4 py-3">
        <a
          href={app.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium hover:underline"
        >
          {app.job_title}
        </a>
        {app.location && (
          <p className="text-xs text-muted-foreground">{app.location}</p>
        )}
      </td>
      <td className="px-4 py-3 text-muted-foreground">
        {app.company ?? "—"}
      </td>
      <td className="px-4 py-3">
        <select
          value={app.status}
          onChange={(e) => onStatusChange(e.target.value as ApplicationStatus)}
          className="rounded-md border border-input bg-background px-2 py-1 text-sm capitalize"
        >
          {APPLICATION_STATUSES.map((s) => (
            <option key={s} value={s} className="capitalize">
              {s}
            </option>
          ))}
        </select>
      </td>
      <td className="px-4 py-3">
        <Button
          variant={app.cover_letter_id != null ? "secondary" : "outline"}
          size="sm"
          onClick={onCoverLetterClick}
          className="gap-1.5"
        >
          <FileText className="h-3.5 w-3.5" />
          {app.cover_letter_id != null ? "View / Edit" : "Create"}
        </Button>
      </td>
    </tr>
  );
}

