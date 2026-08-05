"use client";

import { useState } from "react";
import { ApplicationsPanel } from "@/components/applications/applications-panel";
import { WorkspaceHeader } from "@/components/workspace/workspace-header";

export default function ApplicationsPage() {
  const [applicationCount, setApplicationCount] = useState<number>();

  return (
    <div className="flex flex-col gap-6">
      <WorkspaceHeader active="applications" applicationCount={applicationCount} />
      <ApplicationsPanel onTotalChange={setApplicationCount} />
    </div>
  );
}
