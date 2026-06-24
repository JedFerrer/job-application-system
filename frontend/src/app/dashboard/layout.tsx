import { DashboardNav } from "@/components/nav/dashboard-nav";
import { AuthGuard } from "@/components/auth/auth-guard";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGuard>
      <div className="flex min-h-screen">
        <aside className="hidden w-60 shrink-0 border-r bg-sidebar-background md:block">
          <div className="flex h-14 items-center border-b px-5">
            <span className="text-sm font-semibold tracking-tight">
              Job Application CRM
            </span>
          </div>
          <DashboardNav />
        </aside>
        <main className="flex-1 overflow-x-hidden">
          <div className="mx-auto w-full max-w-5xl px-4 py-8 md:px-8">
            {children}
          </div>
        </main>
      </div>
    </AuthGuard>
  );
}
