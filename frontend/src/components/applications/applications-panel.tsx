"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ChevronDown,
  BookmarkX,
  Check,
  FileText,
  History,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  StickyNote,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const PAGE_SIZE = 25;

function draftFromApplication(app: ApplicationListItem) {
  return {
    next_action_type: (app.next_action_type as ApplicationNextActionType | "") ?? "",
    next_action_note: app.next_action_note ?? "",
    next_action_due_date: app.next_action_due_date ?? "",
  };
}

function draftNotesFromApplication(app: ApplicationListItem) {
  return app.notes ?? "";
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

function formatManualNextActionTypeLabel(type: string | null) {
  if (!type) return null;
  return (
    APPLICATION_NEXT_ACTION_LABELS[type as ApplicationNextActionType] ?? type
  );
}

const MANUAL_NEXT_ACTION_ACTIVITY_LABELS: Record<string, string> = {
  manual_next_action_created: "Next action created",
  manual_next_action_updated: "Next action updated",
  manual_next_action_completed: "Next action completed",
  manual_next_action_cleared: "Next action cleared",
};

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

  const unsaveMutation = useMutation({
    mutationFn: (id: number) => applicationsApi.delete(id),
    onMutate: async (id) => {
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
                items: page.items.filter((application) => application.id !== id),
              })),
            }
          : old,
      );
      return { prev };
    },
    onSuccess: () => toast.success("Removed from saved"),
    onError: (err, _id, context) => {
      if (context?.prev) {
        queryClient.setQueryData(["applications", "infinite"], context.prev);
      }
      toast.error(err instanceof ApiError ? err.message : "Unsave failed");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  const applicationUpdateMutation = useMutation({
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
    onSuccess: (updated, variables) => {
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
      if (
        [
          "next_action_type",
          "next_action_note",
          "next_action_due_date",
          "next_action_completed_at",
        ].some((field) => field in variables.payload)
      ) {
        queryClient.invalidateQueries({
          queryKey: ["applications", updated.id, "activity"],
        });
      }
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
          className="cursor-pointer min-h-9 px-3"
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
            className="cursor-pointer capitalize min-h-9 px-3"
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
                    <th className="px-4 py-3 font-medium">Next action</th>
                    <th className="px-4 py-3 font-medium">Cover letter</th>
                    <th className="px-4 py-3 font-medium text-right">Actions</th>
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
                      onUnsave={() => unsaveMutation.mutate(app.id)}
                      isUnsaving={unsaveMutation.isPending && unsaveMutation.variables === app.id}
                      onApplicationUpdate={(payload) =>
                        applicationUpdateMutation.mutateAsync({
                          id: app.id,
                          payload,
                        })
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
  onUnsave,
  isUnsaving,
  onApplicationUpdate,
  onCoverLetterClick,
}: {
  app: ApplicationListItem;
  onStatusChange: (status: ApplicationStatus) => void;
  onUnsave: () => void;
  isUnsaving: boolean;
  onApplicationUpdate: (payload: ApplicationUpdate) => Promise<unknown>;
  onCoverLetterClick: () => void;
}) {
  const [statusMenuOpen, setStatusMenuOpen] = useState(false);
  const [nextActionMode, setNextActionMode] = useState<"edit" | "new" | null>(
    null,
  );
  const [notesMode, setNotesMode] = useState<"edit" | null>(null);
  const [activityOpen, setActivityOpen] = useState(false);
  const [draft, setDraft] = useState(() => draftFromApplication(app));
  const [notesDraft, setNotesDraft] = useState(() =>
    draftNotesFromApplication(app),
  );

  useEffect(() => {
    if (nextActionMode === null) {
      setDraft(draftFromApplication(app));
    }
    if (notesMode === null) {
      setNotesDraft(draftNotesFromApplication(app));
    }
  }, [app, nextActionMode, notesMode]);

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
  const isNotesOpen = notesMode !== null;

  const startEditing = () => {
    setActivityOpen(false);
    setNotesMode(null);
    setDraft(draftFromApplication(app));
    setNextActionMode("edit");
  };

  const startNewNextAction = () => {
    setActivityOpen(false);
    setNotesMode(null);
    setDraft(emptyNextActionDraft());
    setNextActionMode("new");
  };

  const saveNextAction = async () => {
    try {
      await onApplicationUpdate({
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

  const startEditingNotes = () => {
    setActivityOpen(false);
    setNextActionMode(null);
    setNotesDraft(draftNotesFromApplication(app));
    setNotesMode("edit");
  };

  const saveNotes = async () => {
    try {
      await onApplicationUpdate({
        notes: notesDraft.trim() || null,
      });
      setNotesMode(null);
    } catch {
      // mutation error is surfaced by the toast in the mutation handler
    }
  };

  const clearNotes = async () => {
    try {
      await onApplicationUpdate({
        notes: null,
      });
      setNotesDraft("");
      setNotesMode(null);
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
      await onApplicationUpdate({
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
      await onApplicationUpdate({
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
          <Link href={`/jobs/${app.job_id}`} className="font-medium hover:underline">
            {app.job_title}
          </Link>
          {app.location && (
            <p className="text-xs text-muted-foreground">{app.location}</p>
          )}
        </td>
        <td className="px-4 py-3 text-muted-foreground">
          {app.company ?? "—"}
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
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  if (isNotesOpen) {
                    setNotesMode(null);
                    setNotesDraft(draftNotesFromApplication(app));
                    return;
                  }
                  startEditingNotes();
                }}
                className="gap-1.5"
              >
                <StickyNote className="h-3.5 w-3.5" />
                {isNotesOpen ? "Hide notes" : "Notes"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setNextActionMode(null);
                  setNotesMode(null);
                  setActivityOpen((current) => !current);
                }}
                className="gap-1.5"
              >
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform ${
                    activityOpen ? "rotate-180" : ""
                  }`}
                />
                {activityOpen ? "Hide activity" : "Recent activity"}
              </Button>
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
        <td className="px-4 py-3 text-right align-top">
          <DropdownMenu onOpenChange={(open) => { if (!open) setStatusMenuOpen(false); }}>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Application actions">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {app.status === "saved" && (
                <DropdownMenuItem onSelect={onUnsave} disabled={isUnsaving}>
                  <BookmarkX className="h-4 w-4" />
                  Unsave
                </DropdownMenuItem>
              )}
              <DropdownMenuItem
                onSelect={(event) => {
                  event.preventDefault();
                  setStatusMenuOpen(true);
                }}
              >
                Change status
              </DropdownMenuItem>
              {statusMenuOpen && (
                <>
                  <DropdownMenuSeparator />
                  {APPLICATION_STATUSES.map((status) => (
                    <DropdownMenuItem
                      key={status}
                      onSelect={() => {
                        onStatusChange(status);
                        setStatusMenuOpen(false);
                      }}
                    >
                      {status === app.status ? "✓ " : ""}{status}
                    </DropdownMenuItem>
                  ))}
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
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
                  Next-action note
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
      {isNotesOpen && (
        <tr className="border-b last:border-0 bg-muted/20">
          <td colSpan={5} className="px-4 pb-4 pt-0">
            <div className="rounded-lg border bg-background p-3">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-medium">Application notes</p>
                  <p className="text-xs text-muted-foreground">
                    General notes about the application, company, or process.
                  </p>
                </div>
                {!notesDraft.trim() && (
                  <span className="text-xs text-muted-foreground">
                    No application notes yet.
                  </span>
                )}
              </div>
              <Textarea
                value={notesDraft}
                onChange={(e) => setNotesDraft(e.target.value)}
                placeholder="Add context, reminders, or follow-up details"
                rows={3}
                className="min-h-[96px]"
              />
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" onClick={saveNotes}>
                  Save
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setNotesMode(null);
                    setNotesDraft(draftNotesFromApplication(app));
                  }}
                >
                  Cancel
                </Button>
                <Button size="sm" variant="secondary" onClick={clearNotes}>
                  Clear
                </Button>
              </div>
            </div>
          </td>
        </tr>
      )}
      {activityOpen && (
        <RecentActivityRow applicationId={app.id} isOpen={activityOpen} />
      )}
    </>
  );
}

function RecentActivityRow({
  applicationId,
  isOpen,
}: {
  applicationId: number;
  isOpen: boolean;
}) {
  const activityQuery = useQuery({
    queryKey: ["applications", applicationId, "activity"],
    queryFn: () => applicationsApi.getActivity(applicationId, { limit: 10 }),
    enabled: isOpen,
    retry: false,
  });

  const events = activityQuery.data?.events ?? [];

  return (
    <tr className="border-b last:border-0 bg-muted/20">
      <td colSpan={5} className="px-4 pb-4 pt-0">
        <div className="rounded-lg border bg-background p-3">
          <div className="mb-2 flex items-center gap-2">
            <History className="h-4 w-4 text-muted-foreground" />
            <p className="text-sm font-medium">Recent activity</p>
          </div>
          {activityQuery.isLoading ? (
            <p className="text-xs text-muted-foreground">Loading activity…</p>
          ) : activityQuery.isError ? (
            <p className="text-xs text-muted-foreground">
              Activity could not be loaded.
            </p>
          ) : events.length === 0 ? (
            <p className="text-xs text-muted-foreground">No manual activity yet.</p>
          ) : (
            <ul className="space-y-2">
              {events.map((event) => (
                <li
                  key={event.id}
                  className="rounded-md border border-border/70 bg-muted/20 px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-sm font-medium">
                      {MANUAL_NEXT_ACTION_ACTIVITY_LABELS[event.event_type] ??
                        event.event_type}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {formatDateTimeLabel(event.created_at)}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                    {event.metadata.current.type && (
                      <span>
                        Type: {formatManualNextActionTypeLabel(event.metadata.current.type)}
                      </span>
                    )}
                    {event.metadata.current.due_date && (
                      <span>
                        Due: {formatNextActionDateLabel(event.metadata.current.due_date)}
                      </span>
                    )}
                    <span>
                      {event.metadata.current.has_note ? "Has note" : "No note"}
                    </span>
                    {event.metadata.current.completed && <span>Completed</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </td>
    </tr>
  );
}
