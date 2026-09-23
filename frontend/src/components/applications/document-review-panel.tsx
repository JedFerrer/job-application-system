"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { applicationsApi } from "@/lib/api";
import type { ApplicationPackage, DocumentReview, DocumentReviewSummary } from "@/lib/types";

function key() { return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`; }

function matchesPackage(review: Pick<DocumentReviewSummary, "package_id" | "package_content_sha256">, item: ApplicationPackage) {
  return review.package_id === item.id && review.package_content_sha256 === item.content_sha256;
}

export function DocumentReviewPanel({ applicationId, package: item, initialReview }: { applicationId: number; package: ApplicationPackage; initialReview: DocumentReviewSummary | null }) {
  const [review, setReview] = useState<DocumentReview | null>(null);
  const [lifecycle, setLifecycle] = useState<DocumentReviewSummary | null>(null);
  const [statusLost, setStatusLost] = useState(false);
  const [busy, setBusy] = useState(false);
  const identity = `${applicationId}:${item.id}:${item.content_sha256}:${initialReview?.id ?? "none"}`;
  const identityRef = useRef(identity);

  useEffect(() => {
    identityRef.current = identity;
    const summary = initialReview && matchesPackage(initialReview, item) ? initialReview : null;
    setReview(null);
    setLifecycle(summary);
    setStatusLost(false);
    if (!summary || (summary.state !== "completed" && summary.state !== "failed")) return;
    const requestIdentity = identity;
    void applicationsApi.getDocumentReview(applicationId, item.id, summary.id).then((detail) => {
      if (identityRef.current === requestIdentity && detail.package_content_sha256 === item.content_sha256) setReview(detail);
    }).catch(() => undefined);
  }, [applicationId, item.id, item.content_sha256, initialReview, identity]);

  useEffect(() => {
    if (statusLost || !lifecycle || !matchesPackage(lifecycle, item) || !lifecycle.task_id || (lifecycle.state !== "queued" && lifecycle.state !== "running")) return;
    const requestIdentity = identity;
    let stopped = false;
    const resolvePersisted = async () => {
      try {
        const detail = await applicationsApi.getDocumentReview(applicationId, item.id, lifecycle.id);
        if (stopped || identityRef.current !== requestIdentity || detail.package_content_sha256 !== item.content_sha256) return false;
        setReview(detail);
        setLifecycle((current) => current?.id === lifecycle.id ? { ...current, state: detail.state, outcome: detail.outcome, failure_code: detail.failure_code } : current);
        if (detail.state === "queued" || detail.state === "running") setStatusLost(true);
        return detail.state === "completed" || detail.state === "failed";
      } catch {
        if (!stopped && identityRef.current === requestIdentity) setStatusLost(true);
        return false;
      }
    };
    const poll = async () => {
      try {
        const task = await applicationsApi.getTaskStatus(lifecycle.task_id!);
        if (stopped || identityRef.current !== requestIdentity) return;
        if (task.status === "done" || task.status === "failed") {
          const terminal = await resolvePersisted();
          if (!terminal && !stopped && identityRef.current === requestIdentity) setStatusLost(true);
        }
      } catch {
        await resolvePersisted();
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [applicationId, identity, item, lifecycle, statusLost]);

  const trigger = async (rerun = false) => {
    setBusy(true);
    try {
      const result = await applicationsApi.createDocumentReview(applicationId, item.id, key(), rerun);
      if ("task_id" in result) {
        setReview(null);
        setStatusLost(false);
        setLifecycle({ id: result.review_id, task_id: result.task_id, package_id: item.id, package_content_sha256: item.content_sha256, review_policy_version: initialReview?.review_policy_version ?? "application-document-review-v1", state: result.state, outcome: null, failure_code: null });
      } else {
        setReview(result);
        setLifecycle({ id: result.id, task_id: null, package_id: result.package_id, package_content_sha256: result.package_content_sha256, review_policy_version: result.review_policy_version, state: result.state, outcome: result.outcome, failure_code: result.failure_code });
      }
    } finally { setBusy(false); }
  };
  const acknowledge = async (findingId: string, disposition: "keep_wording" | "will_revise") => { if (review) setReview(await applicationsApi.acknowledgeDocumentReview(applicationId, item.id, review.id, item.content_sha256, [{ finding_id: findingId, disposition }])); };
  const active = lifecycle?.state === "queued" || lifecycle?.state === "running";
  const failure = lifecycle?.state === "failed" || review?.state === "failed";
  const disposition = (findingId: string) => review?.acknowledgments.find((item) => item.finding_id === findingId)?.disposition;

  return <section className="rounded-lg border bg-card p-4"><h2 className="font-semibold">Document review</h2>
    {!lifecycle ? <p className="mt-2 text-sm text-muted-foreground">Not reviewed.</p>
      : statusLost && active ? <p className="mt-2 text-sm text-muted-foreground" role="alert">Review status unavailable. Your documents were not changed.</p>
      : lifecycle.state === "queued" ? <p className="mt-2 text-sm text-muted-foreground">Review queued.</p>
      : lifecycle.state === "running" ? <p className="mt-2 text-sm text-muted-foreground">Reviewing documents…</p>
      : failure ? <p className="mt-2 text-sm text-muted-foreground" role="alert">Review unavailable. Your documents were not changed.</p>
      : !review ? <p className="mt-2 text-sm text-muted-foreground">Loading document review…</p>
      : <div className="mt-3 space-y-3">{review.findings.length ? review.findings.map((finding) => <article key={finding.id} className="rounded border p-3"><p className="font-medium">{finding.category.replaceAll("_", " ")}</p><p className="mt-1 text-sm">{finding.rationale}</p><blockquote className="mt-2 text-sm text-muted-foreground">{finding.excerpt}</blockquote><div className="mt-3 flex gap-2"><Button size="sm" variant="outline" aria-pressed={disposition(finding.id) === "keep_wording"} onClick={() => void acknowledge(finding.id, "keep_wording")}>Keep wording</Button><Button size="sm" variant="outline" aria-pressed={disposition(finding.id) === "will_revise"} onClick={() => void acknowledge(finding.id, "will_revise")}>Will revise</Button></div></article>) : <p className="text-sm text-muted-foreground">No advisory findings.</p>}</div>}
    {lifecycle?.state === "completed" ? <Button className="mt-4" size="sm" onClick={() => void trigger(true)} disabled={busy}>Run review again</Button> : <Button className="mt-4" size="sm" onClick={() => void trigger(false)} disabled={busy || active}>Review documents</Button>}
  </section>;
}
