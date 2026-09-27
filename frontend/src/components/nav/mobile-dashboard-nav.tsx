"use client";

import { useRef } from "react";
import { DashboardNav } from "./dashboard-nav";

export function MobileDashboardNav() {
  const menu = useRef<HTMLDetailsElement>(null);

  return (
    <details ref={menu} className="border-b bg-sidebar-background md:hidden">
      <summary className="cursor-pointer px-4 py-3 text-sm font-semibold">Menu</summary>
      <div onClickCapture={(event) => {
        if ((event.target as HTMLElement).closest("a")) menu.current?.removeAttribute("open");
      }}>
        <DashboardNav />
      </div>
    </details>
  );
}
