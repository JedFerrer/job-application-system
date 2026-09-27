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
import { TailorResumeDialog } from "@/components/resume/tailor-resume-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
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

const STATUS_BADGE_VARIANT: Record<
  ApplicationStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  saved: "secondary",
  applied: "default",
  interview: "default",
  offer: "default",
  rejected: "destructive",
  closed: "outline",
};

function companyInitials(company: string | null): string {
  if (!company) return "?";
  const parts = company.trim().split(/\s+/);
  if (parts.length >= 2 && parts[0] && parts[1]) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }
  return company[0]?.toUpperCase() ?? "?";
}

function companyAvatarClass(company: string | null): string {
  const colors = [
    "bg-blue-900/40 text-blue-300",
    "bg-emerald-900/40 text-emerald-300",
    "bg-purple-900/40 text-purple-300",
    "bg-amber-900/40 text-amber-300",
    "bg-rose-900/40 text-rose-300",
    "bg-cyan-900/40 text-cyan-300",
  ];
  const name = company ?? "";
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) | 0;
  }
  return colors[Math.abs(hash) % colors.length] ?? colors[0] ?? "bg-muted text-foreground";
}

function CompanyLogo({
  company,
  logoUrl,
}: {
  company: string | null;
  logoUrl: string | null;
}) {
  const [logoError, setLogoError] = useState(false);

  if (logoUrl && !logoError) {
    return (
      <img
        src={logoUrl}
        alt={company ? `${company} logo` : "Company logo"}
        className="h-11 w-11 shrink-0 rounded-md bg-muted/30 object-contain md:h-12 md:w-12"
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setLogoError(true)}
      />
    );
  }

  return (
    <div
      aria-hidden="true"
      className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-xs font-semibold md:h-12 md:w-12 ${companyAvatarClass(company)}`}
    >
      {companyInitials(company)}
    </div>
  );
}

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

export function ApplicationsPanel({
  onTotalChange,
}: {
  onTotalChange?: (total: number) => void;
}) {
  const queryClient = useQueryClient();
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [coverLetterJob, setCoverLetterJob] = useState<{
    jobId: number;
    jobTitle: string;
  } | null>(null);
  const [tailorResumeJob, setTailorResumeJob] = useState<{
    jobId: number;
    jobTitle: string;
  } | null>(null);
  const [filterStatus, setFilterStatus] = useState<ApplicationStatus | null>(null);
  const [expandedApplicationId, setExpandedApplicationId] = useState<number | null>(null);
  const [discardPreparationId, setDiscardPreparationId] = useState<number | null>(null);
  const refreshApplicationSurfaces = () => {
    void queryClient.invalidateQueries({ queryKey: ["applications"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard-recommendations"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

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
  const [requestedNextActionId, setRequestedNextActionId] = useState<number | null>(null);

  useEffect(() => {
    const rawId = new URLSearchParams(window.location.search).get(
      "next_action_application_id",
    );
    const parsedId = rawId ? Number(rawId) : NaN;
    if (!Number.isSafeInteger(parsedId) || parsedId <= 0) return;
    setRequestedNextActionId(parsedId);
    const url = new URL(window.location.href);
    url.searchParams.delete("next_action_application_id");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);

  useEffect(() => {
    if (requestedNextActionId === null) return;
    if (allItems.some((item) => item.id === requestedNextActionId)) {
      setExpandedApplicationId(requestedNextActionId);
      return;
    }
    if (appsQuery.hasNextPage && !appsQuery.isFetchingNextPage) {
      void appsQuery.fetchNextPage();
    }
  }, [allItems, appsQuery, requestedNextActionId]);

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

  useEffect(() => {
    if (appsQuery.data) onTotalChange?.(total);
  }, [appsQuery.data, onTotalChange, total]);

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
      queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-recommendations"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
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
    onSuccess: () => {
      toast.success("Removed from saved");
      refreshApplicationSurfaces();
    },
    onError: (err, _id, context) => {
      if (context?.prev) {
        queryClient.setQueryData(["applications", "infinite"], context.prev);
      }
      if (err instanceof ApiError && err.status === 409 && err.message.includes("discarding preparation")) {
        setDiscardPreparationId(_id);
      } else {
        toast.error(err instanceof ApiError ? err.message : "Unsave failed");
      }
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
      queryClient.invalidateQueries({ queryKey: ["dashboard-action-center"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-recommendations"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
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
                <table className="w-full table-auto text-sm md:table-fixed">
                  <colgroup>
                    <col className="md:w-[46%]" />
                    <col />
                    <col className="md:w-[132px]" />
                  </colgroup>
                  <thead className="hidden md:table-header-group">
                    <tr className="border-b text-left text-muted-foreground">
                    <th className="px-4 py-3 font-medium">Application</th>
                    <th className="px-4 py-3 font-medium">Next action</th>
                    <th className="px-4 py-3 pr-6 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="block w-full space-y-3 md:table-row-group md:space-y-0">
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
                      onTailorResumeClick={() =>
                        setTailorResumeJob({
                          jobId: app.job_id,
                          jobTitle: app.job_title,
                        })
                      }
                      isWorkspaceOwner={expandedApplicationId === app.id}
                      openNextAction={requestedNextActionId === app.id}
                      onWorkspaceOpen={() => {
                        setExpandedApplicationId(app.id);
                        if (requestedNextActionId === app.id) {
                          setRequestedNextActionId(null);
                        }
                      }}
                      onWorkspaceClose={() => setExpandedApplicationId(null)}
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
      <TailorResumeDialog
        jobId={tailorResumeJob?.jobId ?? null}
        jobTitle={tailorResumeJob?.jobTitle}
        open={tailorResumeJob != null}
        onOpenChange={(open) => {
          if (!open) setTailorResumeJob(null);
        }}
      />
      <AlertDialog open={discardPreparationId != null} onOpenChange={(open) => { if (!open) setDiscardPreparationId(null); }}>
        <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Discard preparation history?</AlertDialogTitle><AlertDialogDescription>Unsaving this application will remove its preparation choices and immutable package history. This cannot be undone.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Keep application</AlertDialogCancel><AlertDialogAction onClick={() => { if (discardPreparationId != null) applicationsApi.delete(discardPreparationId, { confirmDiscardPreparation: true }).then(() => { setDiscardPreparationId(null); toast.success("Removed from saved"); refreshApplicationSurfaces(); }, (error) => toast.error(error instanceof ApiError ? error.message : "Unsave failed")); }}>Discard and unsave</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
      </AlertDialog>
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
  onTailorResumeClick,
  isWorkspaceOwner,
  openNextAction,
  onWorkspaceOpen,
  onWorkspaceClose,
}: {
  app: ApplicationListItem;
  onStatusChange: (status: ApplicationStatus) => void;
  onUnsave: () => void;
  isUnsaving: boolean;
  onApplicationUpdate: (payload: ApplicationUpdate) => Promise<unknown>;
  onCoverLetterClick: () => void;
  onTailorResumeClick: () => void;
  isWorkspaceOwner: boolean;
  openNextAction: boolean;
  onWorkspaceOpen: () => void;
  onWorkspaceClose: () => void;
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

  useEffect(() => {
    if (!isWorkspaceOwner) {
      setNextActionMode(null);
      setNotesMode(null);
      setActivityOpen(false);
      setStatusMenuOpen(false);
    }
  }, [isWorkspaceOwner]);

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
    onWorkspaceOpen();
    setActivityOpen(false);
    setNotesMode(null);
    setDraft(draftFromApplication(app));
    setNextActionMode("edit");
  };

  const startNewNextAction = () => {
    onWorkspaceOpen();
    setActivityOpen(false);
    setNotesMode(null);
    setDraft(emptyNextActionDraft());
    setNextActionMode("new");
  };

  useEffect(() => {
    if (openNextAction && isWorkspaceOwner && nextActionMode === null) {
      startNewNextAction();
    }
  }, [openNextAction, isWorkspaceOwner, nextActionMode]);

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
      onWorkspaceClose();
    } catch {
      // mutation error is surfaced by the toast in the mutation handler
    }
  };

  const startEditingNotes = () => {
    onWorkspaceOpen();
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
      onWorkspaceClose();
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
      onWorkspaceClose();
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
      onWorkspaceClose();
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
      onWorkspaceClose();
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
      <tr className="block w-full rounded-lg border bg-card p-3 transition-colors hover:bg-muted/20 md:table-row md:rounded-none md:border-0 md:border-b md:border-border/70 md:p-0 md:last:border-b-0">
        <td className="block w-full px-0 py-2 md:table-cell md:px-4 md:py-2.5 md:pr-10">
          <div className="flex items-start gap-3">
            <CompanyLogo company={app.company} logoUrl={app.company_logo} />
            <div className="min-w-0">
              <Link href={`/jobs/${app.job_id}`} className="font-medium hover:underline">
                {app.job_title}
              </Link>
              <Link href={`/applications/${app.id}`} className="mt-1 inline-block text-xs font-medium text-primary hover:underline">
                {app.status === "saved" ? "Prepare application" : "View application"}
              </Link>
              <p className="mt-1 text-xs text-muted-foreground">
                {app.company ?? "Company not listed"}
              </p>
              {app.location && (
                <p className="mt-0.5 text-xs text-muted-foreground">{app.location}</p>
              )}
            </div>
          </div>
        </td>
        <td className="block w-full px-0 py-2 md:table-cell md:px-4 md:py-2.5">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground md:hidden">
            Next action
          </p>
          <div className="min-w-0 space-y-1.5">
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
                  {statusBadgeLabel && !nextActionState.isCompleted && (
                    <Badge
                      variant={nextActionState.isOverdue ? "destructive" : "outline"}
                      className={
                        nextActionState.isDueToday
                          ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                          : undefined
                      }
                    >
                      {statusBadgeLabel}
                    </Badge>
                  )}
                </>
              ) : (
                <span className="text-xs text-muted-foreground">No next action set</span>
              )}
            </div>
            {app.next_action_due_date && (
              <p className="text-xs text-muted-foreground">
                Due {formatNextActionDateLabel(app.next_action_due_date)}
              </p>
            )}
            {app.next_action_note && (
              <p className="max-w-[32rem] text-xs text-muted-foreground line-clamp-1">
                {app.next_action_note}
              </p>
            )}
            {app.next_action_completed_at && (
              <p className="text-xs text-muted-foreground">
                Completed {formatDateTimeLabel(app.next_action_completed_at)}
              </p>
            )}
          </div>
        </td>
        <td className="block w-full px-0 py-2 text-left md:table-cell md:px-4 md:py-2.5 md:pr-6 md:text-right md:align-top">
          <div className="flex flex-wrap items-center justify-between gap-2 md:justify-end">
            {app.next_action_completed_at ? (
              <Button
                variant="outline"
                size="sm"
                onClick={startNewNextAction}
                className="h-8 gap-1.5"
              >
                <Plus className="h-3.5 w-3.5" />
                Add next action
              </Button>
            ) : hasNextAction ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={completeNextAction}
                className="h-8 gap-1.5"
              >
                <Check className="h-3.5 w-3.5" />
                Complete
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={startEditing}
                className="h-8 gap-1.5"
              >
                <Plus className="h-3.5 w-3.5" />
                Set action
              </Button>
            )}
          <DropdownMenu modal={false} onOpenChange={(open) => { if (!open) setStatusMenuOpen(false); }}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`More actions for ${app.job_title}`}
                className="shrink-0"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {hasNextAction && (
                <>
                  <DropdownMenuItem onSelect={startEditing}>
                    <Pencil className="h-4 w-4" />
                    Edit next action
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={clearNextAction}>
                    <Trash2 className="h-4 w-4" />
                    Clear next action
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                </>
              )}
              <DropdownMenuItem
                onSelect={() => {
                  if (isNotesOpen) {
                    setNotesMode(null);
                    setNotesDraft(draftNotesFromApplication(app));
                    onWorkspaceClose();
                  } else {
                    startEditingNotes();
                  }
                }}
              >
                <StickyNote className="h-4 w-4" />
                {isNotesOpen ? "Hide notes" : "Notes"}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  setNextActionMode(null);
                  setNotesMode(null);
                  setActivityOpen((current) => {
                    if (current) onWorkspaceClose();
                    else onWorkspaceOpen();
                    return !current;
                  });
                }}
              >
                <History className="h-4 w-4" />
                {activityOpen ? "Hide activity" : "Recent activity"}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={onCoverLetterClick}>
                <FileText className="h-4 w-4" />
                {app.cover_letter_id != null ? "View / Edit cover letter" : "Create cover letter"}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={onTailorResumeClick}>
                <FileText className="h-4 w-4" />
                Tailor resume
              </DropdownMenuItem>
              <DropdownMenuSeparator />
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
          </div>
        </td>
      </tr>
      {isEditing && (
        <tr className="border-b last:border-0 bg-muted/20">
          <td colSpan={3} className="px-4 pb-4 pt-0">
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
                  onClick={() => {
                    setNextActionMode(null);
                    onWorkspaceClose();
                  }}
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
          <td colSpan={3} className="px-4 pb-4 pt-0">
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
                    onWorkspaceClose();
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
        <RecentActivityRow
          applicationId={app.id}
          isOpen={activityOpen}
          onClose={() => {
            setActivityOpen(false);
            onWorkspaceClose();
          }}
        />
      )}
    </>
  );
}

function RecentActivityRow({
  applicationId,
  isOpen,
  onClose,
}: {
  applicationId: number;
  isOpen: boolean;
  onClose: () => void;
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
      <td colSpan={3} className="px-4 pb-4 pt-0">
        <div className="rounded-lg border bg-background p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <History className="h-4 w-4 text-muted-foreground" />
              <p className="text-sm font-medium">Recent activity</p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="Hide recent activity"
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </Button>
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
