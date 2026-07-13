import {
  APPLICATION_NEXT_ACTION_LABELS,
  type ApplicationNextActionType,
} from "@/lib/types";

export function getTodayIsoDate(now = new Date()) {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

export function formatNextActionDateLabel(value: string) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

export function getNextActionLabel(
  value: string | null | undefined,
): string {
  if (!value) return "Custom";
  return (
    APPLICATION_NEXT_ACTION_LABELS[value as ApplicationNextActionType] ?? value
  );
}

export function getNextActionState(
  nextActionType: string | null | undefined,
  nextActionDueDate: string | null | undefined,
  nextActionCompletedAt: string | null | undefined,
  todayIso = getTodayIsoDate(),
) {
  const actionLabel = getNextActionLabel(nextActionType);
  const dueDate = nextActionDueDate;
  if (!dueDate) {
    const isCompleted = Boolean(nextActionCompletedAt);
    return {
      actionLabel,
      hasDueDate: false,
      isCompleted,
      isOverdue: false,
      isDueToday: false,
      statusLabel: isCompleted ? "Completed" : null,
    };
  }

  const isCompleted = Boolean(nextActionCompletedAt);
  const isOverdue = !isCompleted && dueDate < todayIso;
  const isDueToday = !isCompleted && dueDate === todayIso;
  const statusLabel = isCompleted
    ? "Completed"
    : isOverdue
      ? "Overdue"
      : isDueToday
        ? "Due today"
        : "Due";

  return {
    actionLabel,
    hasDueDate: true,
    isCompleted,
    isOverdue,
    isDueToday,
    statusLabel,
  };
}
