"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Briefcase, FileText, ListChecks, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard/resume", label: "Resume", icon: FileText },
  { href: "/dashboard/jobs", label: "Jobs", icon: Briefcase },
  { href: "/dashboard/matches", label: "Matches", icon: Sparkles },
  { href: "/dashboard/applications", label: "Applications", icon: ListChecks },
];

export function DashboardNav() {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-1 p-3">
      {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
        const isActive = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-secondary text-secondary-foreground"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
