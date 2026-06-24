import { z } from "zod";

// Job-source identifiers the backend ingestion accepts.
export const JOB_SOURCES = [
  "indeed",
  "linkedin",
  "glassdoor",
  "google",
] as const;

export const ingestSchema = z.object({
  search_term: z.string().min(1, "Enter a search term").max(255),
  location: z.string().max(255).optional(),
  country: z.string().max(64).optional(),
  is_remote: z.boolean().optional(),
  hours_old: z.number().int().min(1).max(720).optional(),
  results_wanted: z.number().int().min(1).max(200).optional(),
  sources: z.array(z.enum(JOB_SOURCES)).min(1, "Pick at least one source"),
});

export type IngestForm = z.infer<typeof ingestSchema>;
