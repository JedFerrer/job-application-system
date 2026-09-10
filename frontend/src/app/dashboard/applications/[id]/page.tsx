"use client";

import { useParams } from "next/navigation";
import { ApplicationWorkspace } from "@/components/applications/application-workspace";

export default function ApplicationWorkspacePage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  if (!Number.isSafeInteger(id) || id < 1) return <p className="p-6" role="alert">This application link is invalid.</p>;
  return <ApplicationWorkspace applicationId={id} />;
}
