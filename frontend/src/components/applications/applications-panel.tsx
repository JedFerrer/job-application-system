"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { FileText, Loader2, X } from "lucide-react";
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

const PAGE_SIZE = 25;

export function ApplicationsPanel() {
  const queryClient = useQueryClient();
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [coverLetterJob, setCoverLetterJob] = useState<{
    jobId: number;
    jobTitle: string;
  } | null>(null);
  const [filterStatus, setFilterStatus] = useState<ApplicationStatus | null>(null);

  const hasNextPageRef = useRef(false);
  const isFetchingNextPageRef = useRef(false);
  const fetchNextPageRef = useRef<() => void>(() => {});

  const appsQuery = useInfiniteQuery({
    queryKey: ["applications", "infinite"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      applicationsApi.list({
        limit: PAGE_SIZE,
        cursor: pageParam ?? undefined,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  hasNextPageRef.current = appsQuery.hasNextPage;
  isFetchingNextPageRef.current = appsQuery.isFetchingNextPage;
  fetchNextPageRef.current = appsQuery.fetchNextPage;

  const allItems = useMemo(
    () => appsQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [appsQuery.data],
  );
  const total = appsQuery.data?.pages[0]?.total ?? 0;

  const { filteredItems, statusCounts } = useMemo(() => {
    const counts: Record<ApplicationStatus, number> = {
      saved: 0,
      applied: 0,
      interview: 0,
      offer: 0,
      rejected: 0,
      closed: 0,
    };
    for (const item of allItems) {
      const st = item.status as ApplicationStatus;
      counts[st] = (counts[st] ?? 0) + 1;
    }
    return {
      filteredItems: filterStatus
        ? allItems.filter((a) => a.status === filterStatus)
        : allItems,
      statusCounts: counts,
    };
  }, [allItems, filterStatus]);

  // IntersectionObserver for infinite scroll
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return;
        if (
          entry.isIntersecting &&
          hasNextPageRef.current &&
          !isFetchingNextPageRef.current
        ) {
          fetchNextPageRef.current();
        }
      },
      { rootMargin: "0px 0px 300px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: ApplicationStatus }) =>
      applicationsApi.update(id, { status }),
    onMutate: async ({ id, status }) => {
      await queryClient.cancelQueries({ queryKey: ["applications", "infinite"] });
      const prev = queryClient.getQueryData<{
        pages: ApplicationListResponse[];
        pageParams: (string | null)[];
      }>(["applications", "infinite"]);
      queryClient.setQueryData<{
        pages: ApplicationListResponse[];
        pageParams: (string | null)[];
      }>(["applications", "infinite"], (old) =>
        old
          ? {
              ...old,
              pages: old.pages.map((page) => ({
                ...page,
                items: page.items.map((a) =>
                  a.id === id ? { ...a, status } : a,
                ),
              })),
            }
          : old,
      );
      return { prev };
    },
    onError: (err, _vars, context) => {
      if (context?.prev) {
        queryClient.setQueryData(["applications", "infinite"], context.prev);
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

  if (appsQuery.data && allItems.length === 0) {
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
          All ({total})
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

      {/* Infinite-scroll sentinel */}
      <div ref={sentinelRef} className="h-4" style={{ overflowAnchor: "none" }} />
      {appsQuery.isFetchingNextPage && (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
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

