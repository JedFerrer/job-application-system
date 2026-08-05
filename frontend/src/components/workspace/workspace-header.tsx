import Link from "next/link";

type WorkspaceSection = "applications" | "resumes";

interface WorkspaceHeaderProps {
  active: WorkspaceSection;
  applicationCount?: number;
  resumeCount?: number;
}

const navigation = [
  { key: "applications", label: "Applications", href: "/applications" },
  { key: "resumes", label: "Tailored resumes", href: "/resume" },
] as const;

export function WorkspaceHeader({
  active,
  applicationCount,
  resumeCount,
}: WorkspaceHeaderProps) {
  const counts = { applications: applicationCount, resumes: resumeCount };

  return (
    <div className="flex flex-col gap-4">
      <header>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {active === "applications" ? "Applications" : "Tailored resumes"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {active === "applications"
              ? "Track saved jobs and move them through your pipeline."
              : "Review, edit, and export resumes tailored to specific jobs."}
          </p>
        </div>
      </header>

      <nav
        aria-label="Job management workspace"
        className="flex max-w-full gap-1 overflow-x-auto border-b"
      >
        {navigation.map((item) => {
          const isActive = item.key === active;
          const count = counts[item.key];
          return (
            <Link
              key={item.key}
              href={item.href}
              aria-current={isActive ? "page" : undefined}
              className={`shrink-0 border-b-2 px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:border-border hover:text-foreground"
              }`}
            >
              {item.label}
              {count != null && <span className="ml-1 text-xs">({count})</span>}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
