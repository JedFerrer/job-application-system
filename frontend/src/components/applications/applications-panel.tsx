"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  FileText,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError, applicationsApi } from "@/lib/api";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  APPLICATION_STATUSES,
  APPLICATION_NEXT_ACTION_LABELS,
  APPLICATION_NEXT_ACTION_TYPES,
  type ApplicationListItem,
  type ApplicationListResponse,
  type ApplicationNextActionType,
  type ApplicationUpdate,
  type ApplicationStatus,
} from "@/lib/types";
import {
  formatNextActionDateLabel,
  getNextActionState,
  getTodayIsoDate,
} from "@/lib/next-actions";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const PAGE_SIZE = 25;

function draftFromApplication(app: ApplicationListItem) {
  return {
    next_action_type: (app.next_action_type as ApplicationNextActionType | "") ?? "",
    next_action_note: app.next_action_note ?? "",
    next_action_due_date: app.next_action_due_date ?? "",
  };
}

function emptyNextActionDraft() {
  return {
    next_action_type: "" as ApplicationNextActionType | "",
    next_action_note: "",
    next_action_due_date: "",
  };
}

function formatDateTimeLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

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

  const nextActionMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: ApplicationUpdate }) =>
      applicationsApi.update(id, payload),
    onMutate: async ({ id, payload }) => {
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
                  a.id === id ? { ...a, ...payload } : a,
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
    onSuccess: (updated) => {
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
                  a.id === updated.id
                    ? {
                        ...a,
                        status: updated.status,
                        cover_letter_id: updated.cover_letter_id,
                        notes: updated.notes,
                        next_action_type: updated.next_action_type,
                        next_action_note: updated.next_action_note,
                        next_action_due_date: updated.next_action_due_date,
                        next_action_completed_at:
                          updated.next_action_completed_at,
                        updated_at: updated.updated_at,
                      }
                    : a,
                ),
              })),
            }
          : old,
      );
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["applications", "infinite"] });
      queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.invalidateQueries({ queryKey: ["home-next-actions"] });
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
                    <th className="px-4 py-3 font-medium">Next action</th>
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
                      onNextActionChange={(payload) =>
                        nextActionMutation.mutateAsync({ id: app.id, payload })
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
  onNextActionChange,
  onCoverLetterClick,
}: {
  app: ApplicationListItem;
  onStatusChange: (status: ApplicationStatus) => void;
  onNextActionChange: (payload: ApplicationUpdate) => Promise<unknown>;
  onCoverLetterClick: () => void;
}) {
  const [nextActionMode, setNextActionMode] = useState<"edit" | "new" | null>(
    null,
  );
  const [draft, setDraft] = useState(() => draftFromApplication(app));

  useEffect(() => {
    if (nextActionMode === null) {
      setDraft(draftFromApplication(app));
    }
  }, [app, nextActionMode]);

  const hasNextAction = Boolean(
    app.next_action_type ||
      app.next_action_note ||
      app.next_action_due_date ||
      app.next_action_completed_at,
  );
  const isEditing = nextActionMode !== null;
  const todayIso = getTodayIsoDate();
  const nextActionState = getNextActionState(
    app.next_action_type,
    app.next_action_due_date,
    app.next_action_completed_at,
    todayIso,
  );
  const statusBadgeLabel = nextActionState.isCompleted
    ? null
    : nextActionState.statusLabel;

  const startEditing = () => {
    setDraft(draftFromApplication(app));
    setNextActionMode("edit");
  };

  const startNewNextAction = () => {
    setDraft(emptyNextActionDraft());
    setNextActionMode("new");
  };

  const saveNextAction = async () => {
    try {
      await onNextActionChange({
        next_action_type: draft.next_action_type || null,
        next_action_note: draft.next_action_note.trim() || null,
        next_action_due_date: draft.next_action_due_date || null,
        next_action_completed_at:
          nextActionMode === "new" ? null : app.next_action_completed_at,
      });
      setNextActionMode(null);
    } catch {
      // mutation error is surfaced by the toast in the mutation handler
    }
  };

  const completeNextAction = async () => {
    if (!hasNextAction) return;
    try {
      const currentType = isEditing ? draft.next_action_type : app.next_action_type;
      const currentNote = isEditing ? draft.next_action_note : app.next_action_note;
      const currentDueDate = isEditing ? draft.next_action_due_date : app.next_action_due_date;
      await onNextActionChange({
        next_action_type: currentType ? (currentType as ApplicationNextActionType) : null,
        next_action_note: currentNote || null,
        next_action_due_date: currentDueDate || null,
        next_action_completed_at: new Date().toISOString(),
      });
      setNextActionMode(null);
    } catch {
      // mutation error is surfaced by the toast in the mutation handler
    }
  };

  const clearNextAction = async () => {
    try {
      await onNextActionChange({
        next_action_type: null,
        next_action_note: null,
        next_action_due_date: null,
        next_action_completed_at: null,
      });
      setDraft({
        ...emptyNextActionDraft(),
      });
      setNextActionMode(null);
    } catch {
      // mutation error is surfaced by the toast in the mutation handler
    }
  };

  const canSave =
    draft.next_action_type !== "" ||
    draft.next_action_note.trim() !== "" ||
    draft.next_action_due_date !== "";

  return (
    <>
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
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              {hasNextAction ? (
                <>
                  <Badge
                    variant={nextActionState.isCompleted ? "secondary" : "outline"}
                    className="capitalize"
                  >
                    {nextActionState.actionLabel}
                  </Badge>
                  {nextActionState.isCompleted && (
                    <Badge variant="secondary">Completed</Badge>
                  )}
                  {statusBadgeLabel && (
                    <Badge
                      variant={
                        nextActionState.isOverdue ? "destructive" : "outline"
                      }
                    >
                      {statusBadgeLabel}
                      {app.next_action_due_date && (
                        <span className="ml-1 font-normal">
                          {formatNextActionDateLabel(app.next_action_due_date)}
                        </span>
                      )}
                    </Badge>
                  )}
                </>
              ) : (
                <span className="text-xs text-muted-foreground">No next action set</span>
              )}
            </div>
            {app.next_action_note && (
              <p className="max-w-[18rem] text-xs text-muted-foreground line-clamp-2">
                {app.next_action_note}
              </p>
            )}
            {app.next_action_completed_at && (
              <p className="text-xs text-muted-foreground">
                Completed {formatDateTimeLabel(app.next_action_completed_at)}
              </p>
            )}
            {app.next_action_completed_at && app.next_action_due_date && (
              <p className="text-xs text-muted-foreground">
                Due {formatNextActionDateLabel(app.next_action_due_date)}
              </p>
            )}
            <div className="flex flex-wrap gap-1.5">
              <Button
                variant="outline"
                size="sm"
                onClick={startEditing}
                className="gap-1.5"
              >
                <Pencil className="h-3.5 w-3.5" />
                {hasNextAction ? "Edit" : "Set"}
              </Button>
              {!app.next_action_completed_at && hasNextAction && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={completeNextAction}
                  className="gap-1.5"
                >
                  <Check className="h-3.5 w-3.5" />
                  Complete
                </Button>
              )}
              {app.next_action_completed_at && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={startNewNextAction}
                  className="gap-1.5"
                >
                  <Plus className="h-3.5 w-3.5" />
                  New next action
                </Button>
              )}
            </div>
          </div>
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
      {isEditing && (
        <tr className="border-b last:border-0 bg-muted/20">
          <td colSpan={5} className="px-4 pb-4 pt-0">
            <div className="rounded-lg border bg-background p-3">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-medium">
                    {nextActionMode === "new"
                      ? "Create a new next action"
                      : "Next action"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {nextActionMode === "new"
                      ? "This starts a fresh active next action and clears the completed state."
                      : "Keep it short, truthful, and current. Clear it when it stops being the next step."}
                  </p>
                </div>
                {app.next_action_completed_at && (
                  <Badge variant="secondary">Completed</Badge>
                )}
              </div>
              <div className="grid gap-3 md:grid-cols-[180px_1fr_160px]">
                <label className="flex flex-col gap-1 text-xs font-medium">
                  Type
                  <select
                    value={draft.next_action_type}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        next_action_type: e.target.value as ApplicationNextActionType | "",
                      }))
                    }
                    className="rounded-md border border-input bg-background px-2 py-2 text-sm"
                  >
                    <option value="">No next action</option>
                    {APPLICATION_NEXT_ACTION_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {APPLICATION_NEXT_ACTION_LABELS[type]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium">
                  Note
                  <Textarea
                    value={draft.next_action_note}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        next_action_note: e.target.value,
                      }))
                    }
                    placeholder="Short next step or context"
                    rows={2}
                    className="min-h-[72px]"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium">
                  Due date
                  <Input
                    type="date"
                    value={draft.next_action_due_date}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        next_action_due_date: e.target.value,
                      }))
                    }
                  />
                </label>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  size="sm"
                  onClick={saveNextAction}
                  disabled={!canSave}
                >
                  {nextActionMode === "new"
                    ? "Save new next action"
                    : "Save next action"}
                </Button>
                {!app.next_action_completed_at && hasNextAction && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={completeNextAction}
                    className="gap-1.5"
                  >
                    <Check className="h-3.5 w-3.5" />
                    Mark complete
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={clearNextAction}
                  className="gap-1.5"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Clear
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setNextActionMode(null)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
