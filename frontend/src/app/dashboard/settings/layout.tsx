"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const SETTINGS_TABS = [
  { href: "/settings/profile", label: "Profile" },
  { href: "/settings/preferences", label: "Preferences" },
  { href: "/settings/readiness", label: "Readiness" },
  { href: "/settings/connected-apps", label: "Connected Apps" },
];

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Manage your profile and job search preferences.
        </p>
      </header>

      {/* Tab bar */}
      <div className="flex border-b">
        {SETTINGS_TABS.map(({ href, label }) => {
          const dashboardPath = `/dashboard${href}`;
          const isActive =
            pathname === href ||
            pathname.startsWith(`${href}/`) ||
            pathname === dashboardPath ||
            pathname.startsWith(`${dashboardPath}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
                isActive
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </Link>
          );
        })}
      </div>

      {children}
    </div>
  );
}
