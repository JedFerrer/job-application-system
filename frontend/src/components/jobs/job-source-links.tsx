"use client";

import { ExternalLink } from "lucide-react";

import type { JobSourceLink } from "@/lib/types";

function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  const protocol = trimmed.slice(0, 8).toLowerCase();
  if (!protocol.startsWith("https://") && !protocol.startsWith("http://")) return null;
  if (/\s/.test(trimmed)) return null;
  return trimmed;
}

function listingUrl(link: JobSourceLink): string | null {
  return safeHttpUrl(link.canonical_url) ?? safeHttpUrl(link.source_url);
}

export function JobSourceLinks({ links }: { links: JobSourceLink[] }) {
  const listings = links.flatMap((link) => {
    const url = listingUrl(link);
    if (!url) return [];
    return [{ source: link.source, url }];
  });
  const listingHrefs = new Set(listings.map((item) => item.url));
  const applyUrls = [
    ...new Set(
      links
        .map((link) => safeHttpUrl(link.external_apply_url))
        .filter((url): url is string => url != null && !listingHrefs.has(url)),
    ),
  ];
  if (listings.length < 2) return null;
  return (
    <section aria-label="Alternate job links" className="rounded-lg border p-4">
      <h2 className="text-sm font-semibold">Alternate job links</h2>
      <ul className="mt-2 space-y-2 text-sm">
        {listings.map((item) => (
          <li key={`listing-${item.source}-${item.url}`}>
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> Open via {item.source.replace(/_/g, " ")}
            </a>
          </li>
        ))}
        {applyUrls.map((url) => (
          <li key={`apply-${url}`}>
            <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> Open employer application
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
