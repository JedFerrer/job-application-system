"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, applicationsApi } from "@/lib/api";
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

export function ApplicationsPanel() {
  const queryClient = useQueryClient();

  const appsQuery = useQuery({
    queryKey: ["applications"],
    queryFn: applicationsApi.list,
  });

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

  return (
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
              {appsQuery.data?.items.map((app) => (
                <ApplicationRow
                  key={app.id}
                  app={app}
                  onStatusChange={(status) =>
                    statusMutation.mutate({ id: app.id, status })
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function ApplicationRow({
  app,
  onStatusChange,
}: {
  app: ApplicationListItem;
  onStatusChange: (status: ApplicationStatus) => void;
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
      <td className="px-4 py-3 text-muted-foreground">
        {app.cover_letter_id != null ? `#${app.cover_letter_id}` : "—"}
      </td>
    </tr>
  );
}
