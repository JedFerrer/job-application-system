"use client";

/**
 * Controls compiled only into the isolated Unit 1A Browser QA dashboard.
 * They use the normal authenticated ingestion endpoint and task watcher; the
 * reserved source names are rejected outside the separately guarded QA API.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { jobsApi } from "@/lib/api";
import { useJobsTask } from "@/context/jobs-task-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const enabled = process.env.NEXT_PUBLIC_UNIT_1A_BROWSER_QA === "true";

const scenarios = [
  {
    id: "mixed",
    label: "Run mixed outcomes",
    sources: ["qa-unit-1a-success", "qa-unit-1a-failure", "qa-unit-1a-uncertain-empty"],
  },
  {
    id: "failed",
    label: "Run total source failure",
    sources: ["qa-unit-1a-total-failure-a", "qa-unit-1a-total-failure-b"],
  },
  {
    id: "empty",
    label: "Run uncertain empty",
    sources: ["qa-unit-1a-uncertain-empty"],
  },
] as const;

export function Unit1AQaControls() {
  const { isRunning, progressMsg, setTask } = useJobsTask();
  const [error, setError] = useState<string | null>(null);
  const capabilities = useQuery({
    queryKey: ["unit-1a-qa-source-capabilities"],
    queryFn: jobsApi.listSourceCapabilities,
    enabled,
  });

  if (!enabled) return null;

  const run = async (sources: readonly string[]) => {
    setError(null);
    try {
      const queued = await jobsApi.ingest({
        search_term: "Unit 1A QA",
        country: "QA",
        results_wanted: 1,
        sources: [...sources],
      });
      setTask(queued.task_id, [...sources]);
    } catch {
      setError("The isolated QA run could not be started.");
    }
  };

  return (
    <Card data-testid="unit-1a-qa-controls" className="border-dashed border-amber-500/60">
      <CardContent className="flex flex-col gap-3 p-4">
        <div>
          <h2 className="text-sm font-semibold">Unit 1A controlled Browser QA</h2>
          <p className="text-xs text-muted-foreground">
            Local synthetic fixtures only. No external job provider is contacted.
          </p>
        </div>
        <p className="text-xs text-muted-foreground" data-testid="unit-1a-qa-capabilities">
          {capabilities.isSuccess
            ? `${capabilities.data.length} controlled source contracts available`
            : "Loading controlled source contracts…"}
        </p>
        <div className="flex flex-wrap gap-2">
          {scenarios.map((scenario) => (
            <Button
              key={scenario.id}
              size="sm"
              variant="outline"
              disabled={isRunning}
              onClick={() => void run(scenario.sources)}
            >
              {scenario.label}
            </Button>
          ))}
        </div>
        {isRunning && <p className="text-xs text-muted-foreground">{progressMsg ?? "Starting controlled run…"}</p>}
        {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
      </CardContent>
    </Card>
  );
}
