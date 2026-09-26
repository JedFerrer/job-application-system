import type { Ref } from "react";
import type { ApplicationPackage } from "@/lib/types";

function json(value: unknown) { return JSON.stringify(value, null, 2); }

/** Renders only the persisted package snapshot, never current editor state. */
export function PackagePreview({ package: item, headingRef, current = false }: { package: ApplicationPackage; headingRef?: Ref<HTMLHeadingElement>; current?: boolean }) {
  const content = item.content as Record<string, unknown>;
  const job = content.job as Record<string, unknown> | undefined;
  const resume = content.resume as Record<string, unknown> | undefined;
  const letter = content.cover_letter as Record<string, unknown> | undefined;
  const questions = content.manual_questions as unknown[] | undefined;
  return <section aria-labelledby="package-preview-heading" className="rounded-lg border bg-card p-4">
    <div className="flex flex-wrap items-baseline justify-between gap-2"><div><h2 id="package-preview-heading" ref={headingRef} tabIndex={headingRef ? -1 : undefined} className="font-semibold">{current ? "Current package preview" : "Frozen package preview"}</h2><p className="text-sm text-muted-foreground">Revision {item.revision} · {item.content_sha256.slice(0, 12)}</p></div></div>
    <div className="mt-4 max-h-[60vh] space-y-4 overflow-y-auto pr-1" tabIndex={0} aria-label="Frozen package content">
      {job ? <details open><summary className="cursor-pointer font-medium">Job context</summary><pre className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{json(job)}</pre></details> : null}
      {resume ? <details open><summary className="cursor-pointer font-medium">Tailored resume</summary><pre className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{json(resume)}</pre></details> : null}
      <details open><summary className="cursor-pointer font-medium">Cover letter</summary><pre className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{letter ? json(letter) : "Not included"}</pre></details>
      {questions?.length ? <details><summary className="cursor-pointer font-medium">Manual questions</summary><pre className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{json(questions)}</pre></details> : null}
    </div>
  </section>;
}
