---
applyTo: "frontend/**"
---

# Frontend Engineering Instructions

> Stack: Next.js 16.2 · React 19.2 · TypeScript 5.x · TailwindCSS 4.x · shadcn/ui · TanStack Query v5 · Zustand · Zod · React Hook Form

---

## 1. TypeScript Standards

- **TypeScript strict mode** is mandatory. Never disable `strict` or individual checks in `tsconfig.json`.
- Prefer `interface` for component props and API shapes. Use `type` for unions, intersections, and utility types.
- Never use `any`. Use `unknown` when the type is genuinely unknown; narrow it explicitly.
- Use `satisfies` for config objects where you want type-checking without widening:

```ts
const config = {
  apiUrl: process.env.NEXT_PUBLIC_API_URL!,
  pageSize: 20,
} satisfies DashboardConfig;
```

- Enable `noUncheckedIndexedAccess` in `tsconfig.json` — forces safe array access.

---

## 2. Next.js 16.2 App Router

### File Conventions

| File | Purpose |
|------|---------|
| `page.tsx` | Route UI — Server Component by default |
| `layout.tsx` | Persistent UI shell — Server Component |
| `loading.tsx` | Suspense fallback for the route |
| `error.tsx` | Error boundary — must be `"use client"` |
| `not-found.tsx` | 404 handler for the route segment |
| `route.ts` | API Route Handler (replaces `pages/api/`) |

### Server vs Client Components

**Default to Server Components.** Add `"use client"` only when you need:
- `useState`, `useReducer`, `useEffect`
- Browser APIs (`window`, `document`, `localStorage`)
- Event listeners and interactive handlers
- TanStack Query hooks, Zustand stores

```tsx
// ✅ Server Component — data fetching at top, no client directive
// app/dashboard/jobs/page.tsx
import { JobList } from "@/components/jobs/job-list";
import { getJobs } from "@/lib/api/jobs";

export default async function JobsPage() {
  const jobs = await getJobs();  // direct server-side fetch
  return <JobList initialJobs={jobs} />;
}

// ✅ Client Component — only where interactivity is needed
// components/jobs/job-filter.tsx
"use client";

import { useState } from "react";

export function JobFilter({ onFilter }: JobFilterProps) {
  const [status, setStatus] = useState<ApplicationStatus>("all");
  // ...
}
```

### Data Caching with `use cache`

Use the `use cache` directive (stable in 16.2) for server-side caching:

```tsx
// Cache job listings for 60 seconds
export async function getJobListings(query: string) {
  "use cache";
  // Result is automatically cached and reused across requests
  const response = await fetch(`${process.env.API_URL}/api/v1/jobs?q=${query}`);
  return response.json();
}
```

### Server Actions

- Use Server Actions for mutations (form submissions, data updates) when possible — avoids extra API Route boilerplate.
- Always validate inputs with Zod inside Server Actions — never trust client data.
- Return a discriminated union `ActionResult<T>` — never throw to the client.

```tsx
// app/dashboard/applications/actions.ts
"use server";

import { z } from "zod";
import { revalidatePath } from "next/cache";

const CreateApplicationSchema = z.object({
  jobId: z.number().int().positive(),
  resumeId: z.string().uuid(),
});

type ActionResult<T> =
  | { success: true; data: T }
  | { success: false; error: string };

export async function createApplication(
  formData: FormData
): Promise<ActionResult<Application>> {
  const parsed = CreateApplicationSchema.safeParse({
    jobId: Number(formData.get("jobId")),
    resumeId: formData.get("resumeId"),
  });

  if (!parsed.success) {
    return { success: false, error: parsed.error.flatten().formErrors.join(", ") };
  }

  try {
    const application = await applicationService.create(parsed.data);
    revalidatePath("/dashboard/applications");
    return { success: true, data: application };
  } catch {
    return { success: false, error: "Failed to create application." };
  }
}
```

---

## 3. Component Architecture

### Directory Structure

```
frontend/
├── app/                      # Next.js App Router routes
│   ├── (auth)/               # Route group — login, register
│   ├── dashboard/
│   │   ├── jobs/
│   │   ├── applications/
│   │   ├── resume/
│   │   └── layout.tsx
│   └── api/                  # Route Handlers (thin wrappers only)
├── components/
│   ├── ui/                   # shadcn/ui primitives (do not edit directly)
│   ├── jobs/                 # Domain-specific components
│   ├── applications/
│   ├── resume/
│   └── shared/               # Truly reusable: ErrorBoundary, EmptyState, etc.
├── hooks/                    # Custom client-side hooks
├── lib/
│   ├── api/                  # API client functions (fetch wrappers)
│   ├── validators/           # Zod schemas
│   └── utils.ts
├── stores/                   # Zustand stores (client state only)
└── types/                    # Shared TypeScript interfaces
```

### Component Rules

- **One component per file.** File name matches component name (PascalCase).
- Props interface defined in the same file, named `{ComponentName}Props`.
- Extract sub-components into their own files when they exceed ~80 lines or are reused.
- Use `React.memo()` only when profiling shows it's necessary — don't premature-optimize.
- Never put data fetching logic inside components that are not `async` Server Components or custom hooks.

```tsx
// ✅ Clean component with clear separation
interface JobCardProps {
  job: Job;
  onApply: (jobId: number) => void;
}

export function JobCard({ job, onApply }: JobCardProps) {
  return (
    <div className="rounded-lg border p-4">
      <h3 className="font-semibold">{job.title}</h3>
      <p className="text-sm text-muted-foreground">{job.company}</p>
      <button onClick={() => onApply(job.id)}>Apply</button>
    </div>
  );
}
```

---

## 4. TanStack Query v5 (Data Fetching)

- Use `useQuery` for all client-side data fetching. Never use `useEffect` + `fetch` directly.
- Use `useMutation` for all data mutations from client components.
- Use `queryKey` arrays that fully describe the request (include all params):

```tsx
// ✅ Correct query key — includes all relevant params
const { data: jobs, isLoading } = useQuery({
  queryKey: ["jobs", { status: filter.status, page: filter.page }],
  queryFn: () => fetchJobs(filter),
  staleTime: 30_000,  // 30 seconds
});

// ✅ Mutation with optimistic update
const { mutate: updateStatus } = useMutation({
  mutationFn: (args: UpdateStatusArgs) => updateApplicationStatus(args),
  onMutate: async ({ applicationId, status }) => {
    await queryClient.cancelQueries({ queryKey: ["applications"] });
    const prev = queryClient.getQueryData(["applications"]);
    queryClient.setQueryData(["applications"], (old: Application[]) =>
      old.map((a) => (a.id === applicationId ? { ...a, status } : a))
    );
    return { prev };
  },
  onError: (_err, _args, context) => {
    queryClient.setQueryData(["applications"], context?.prev);
  },
  onSettled: () => {
    queryClient.invalidateQueries({ queryKey: ["applications"] });
  },
});
```

---

## 5. Zustand (Client State)

- Use Zustand only for **UI state that doesn't belong in the server** (filters, sidebar open/close, selected items, multi-step form state).
- Do not store server data (job listings, applications) in Zustand — use TanStack Query.
- Use `immer` middleware for complex nested updates.
- Slice stores by domain — one store per domain area.

```tsx
// stores/job-filter-store.ts
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

interface JobFilterState {
  status: ApplicationStatus;
  location: string;
  isRemote: boolean;
  setFilter: <K extends keyof JobFilterState>(key: K, value: JobFilterState[K]) => void;
  reset: () => void;
}

const initialState = { status: "all" as ApplicationStatus, location: "", isRemote: false };

export const useJobFilterStore = create<JobFilterState>()(
  immer((set) => ({
    ...initialState,
    setFilter: (key, value) => set((state) => { state[key] = value; }),
    reset: () => set(initialState),
  }))
);
```

---

## 6. Forms: Zod + React Hook Form

- All user-facing forms must use `react-hook-form` + `zod` via `@hookform/resolvers/zod`.
- Define the schema first, derive the TypeScript type from it — never duplicate.
- Show field-level error messages immediately on blur.

```tsx
"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

const SearchJobsSchema = z.object({
  query: z.string().min(2, "Enter at least 2 characters"),
  location: z.string().optional(),
  isRemote: z.boolean().default(false),
});

type SearchJobsForm = z.infer<typeof SearchJobsSchema>;

export function JobSearchForm() {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SearchJobsForm>({ resolver: zodResolver(SearchJobsSchema) });

  const onSubmit = async (data: SearchJobsForm) => {
    await triggerJobSearch(data);
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <input {...register("query")} placeholder="Software engineer..." />
      {errors.query && <p className="text-destructive text-sm">{errors.query.message}</p>}
      <button type="submit" disabled={isSubmitting}>Search</button>
    </form>
  );
}
```

---

## 7. Security (OWASP Frontend)

### XSS Prevention (OWASP A03)

- **Never use `dangerouslySetInnerHTML`** unless the content has been sanitized with `DOMPurify` server-side first.
- Treat all data from API responses as untrusted. Never call `eval()`.
- When rendering user-generated content (resume text, job descriptions), use a sanitized markdown renderer:

```tsx
import DOMPurify from "isomorphic-dompurify";

// Only safe attributes — no script, no event handlers
const clean = DOMPurify.sanitize(userContent, { ALLOWED_TAGS: ["p", "b", "i", "ul", "li"] });
```

### Content Security Policy (OWASP A05)

Set CSP headers in `next.config.ts`. Restrict scripts, styles, and connections:

```ts
// next.config.ts
const cspHeader = `
  default-src 'self';
  script-src 'self' 'nonce-{nonce}';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data: https://;
  connect-src 'self' ${process.env.NEXT_PUBLIC_API_URL};
  frame-ancestors 'none';
`.replace(/\n/g, " ");
```

Use a middleware to inject a per-request nonce for inline scripts.

### CSRF Protection (OWASP A01)

- Server Actions have built-in CSRF protection in Next.js 16.x (Origin header validation).
- For direct API calls, include the `X-Requested-With: XMLHttpRequest` header and validate it server-side.
- Use `SameSite=Strict` cookies for auth tokens — mitigates cross-site request forgery.

### Sensitive Data in URLs

- Never put sensitive data (tokens, user IDs, email addresses) in URL query parameters.
- Use POST for any request carrying credentials or sensitive form data.

### Dependency Security

- Run `npm audit` in CI. Fail the build on high/critical vulnerabilities.
- Pin exact versions in `package.json` for production dependencies.
- Regularly update dependencies: `npx npm-check-updates -u`.

### Environment Variables

- `NEXT_PUBLIC_*` variables are **exposed to the browser**. Never put secrets there.
- Server-only secrets (API keys, DB URLs) go in non-prefixed env vars and are only used in Server Components, Server Actions, and Route Handlers.

```ts
// ✅ Safe — server-only
const apiKey = process.env.OPENAI_API_KEY;

// ❌ EXPOSED to browser bundle
const apiKey = process.env.NEXT_PUBLIC_OPENAI_API_KEY;
```

---

## 8. Error Boundaries & Loading States

- Every route segment must have a `loading.tsx` (Suspense fallback).
- Every route segment that fetches data must have an `error.tsx` (error boundary).
- `error.tsx` must be a Client Component — it receives the `error` and `reset` props.

```tsx
// app/dashboard/jobs/error.tsx
"use client";

export default function JobsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-4 p-8">
      <p className="text-destructive">Failed to load jobs: {error.message}</p>
      <button onClick={reset} className="btn-secondary">Try again</button>
    </div>
  );
}
```

- Use `React.Suspense` with meaningful skeleton fallbacks — not blank screens or spinners-only.

---

## 9. Accessibility (a11y)

- All interactive elements must be reachable by keyboard.
- Use semantic HTML: `<button>` for actions, `<a>` for navigation, `<header>`, `<main>`, `<nav>`.
- Every `<img>` must have a descriptive `alt` attribute. Decorative images use `alt=""`.
- Forms must associate `<label>` with `<input>` via `htmlFor`.
- Dialogs/modals must trap focus and support `Escape` to close — shadcn/ui `Dialog` handles this by default.
- Use `aria-live` regions for status updates (e.g., "Application submitted successfully").

---

## 10. Performance

- Use `next/image` for all images — automatic WebP conversion, lazy loading, and size optimization.
- Use `next/link` for all internal navigation — prefetching and soft navigation.
- Use `next/font` for fonts — eliminates layout shift (no external Google Fonts requests).
- Lazy-load heavy client components (resume editor, PDF viewer) with `dynamic`:

```tsx
const ResumeEditor = dynamic(() => import("@/components/resume/editor"), {
  loading: () => <EditorSkeleton />,
  ssr: false,  // disable SSR for heavy client-only components
});
```

- Enable React Compiler in `next.config.ts` (stable in 16.2) — automatic memoization:

```ts
experimental: {
  reactCompiler: true,
}
```

---

## 11. Code Quality

### ESLint + Prettier

```json
// .eslintrc.json — extend Next.js strict rules
{
  "extends": ["next/core-web-vitals", "next/typescript"],
  "rules": {
    "no-console": "warn",
    "@typescript-eslint/no-explicit-any": "error",
    "@typescript-eslint/no-unused-vars": "error"
  }
}
```

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Components | PascalCase | `JobCard`, `ApplicationTable` |
| Files (components) | kebab-case | `job-card.tsx`, `application-table.tsx` |
| Hooks | `use` prefix | `useJobFilters`, `useApplicationStatus` |
| Stores | camelCase | `useJobFilterStore` |
| Zod schemas | PascalCase + `Schema` | `CreateApplicationSchema` |
| Types/Interfaces | PascalCase | `Application`, `JobListing` |

### What NOT to Do

- No `// @ts-ignore` or `// @ts-nocheck` — fix the type error.
- No `as unknown as X` double casts — signals a type design problem.
- No raw `fetch` inside `useEffect` — use TanStack Query.
- No inline `style={{}}` for layout (use Tailwind classes). Acceptable only for truly dynamic CSS values.
- No hardcoded URLs — use env vars and a typed API client.

---

## 12. API Client Pattern

Define a typed, centralized API client — never scatter `fetch` calls across components:

```ts
// lib/api/client.ts
const BASE_URL = process.env.NEXT_PUBLIC_API_URL;

async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    credentials: "include",  // send httpOnly auth cookies
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new APIError(response.status, error.detail ?? "Request failed");
  }

  return response.json() as Promise<T>;
}

// lib/api/jobs.ts
export const jobsApi = {
  list: (params: JobSearchParams) =>
    apiFetch<PaginatedResponse<Job>>(`/api/v1/jobs?${new URLSearchParams(params as Record<string, string>)}`),
  getById: (id: number) =>
    apiFetch<Job>(`/api/v1/jobs/${id}`),
};
```

---

## 13. Key Dependencies

```json
{
  "dependencies": {
    "next": "^16.2.0",
    "react": "^19.2.0",
    "react-dom": "^19.2.0",
    "@tanstack/react-query": "^5.65.0",
    "zustand": "^5.0.0",
    "zod": "^3.24.0",
    "react-hook-form": "^7.54.0",
    "@hookform/resolvers": "^3.9.0",
    "isomorphic-dompurify": "^2.19.0",
    "next-themes": "^0.4.4"
  },
  "devDependencies": {
    "typescript": "^5.8.0",
    "@types/react": "^19.0.0",
    "eslint": "^9.0.0",
    "eslint-config-next": "^16.2.0",
    "@testing-library/react": "^16.2.0",
    "@testing-library/user-event": "^14.5.0",
    "vitest": "^3.1.0",
    "@vitejs/plugin-react": "^4.4.0"
  }
}
```
