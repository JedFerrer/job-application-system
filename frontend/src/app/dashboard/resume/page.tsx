"use client";

import { useState } from "react";
import { ResumePanel } from "@/components/resume/resume-panel";
import { WorkspaceHeader } from "@/components/workspace/workspace-header";

export default function ResumePage() {
  const [resumeCount, setResumeCount] = useState<number>();

  return (
    <div className="flex flex-col gap-6">
      <WorkspaceHeader active="resumes" resumeCount={resumeCount} />
      <ResumePanel onDraftCountChange={setResumeCount} />
    </div>
  );
}
