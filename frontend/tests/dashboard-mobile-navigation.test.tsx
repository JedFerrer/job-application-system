import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import DashboardLayout from "../src/app/dashboard/layout";

vi.mock("@/components/auth/auth-guard", () => ({
  AuthGuard: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/components/nav/dashboard-nav", () => ({
  DashboardNav: () => <nav aria-label="Evaluation navigation"><a href="/applications">Applications</a></nav>,
}));

it("keeps the application navigation available in the mobile menu", () => {
  const document = new DOMParser().parseFromString(
    renderToStaticMarkup(<DashboardLayout><p>Workspace</p></DashboardLayout>),
    "text/html",
  );
  const mobileMenu = document.querySelector("details.md\\:hidden");
  expect(mobileMenu?.querySelector("summary")?.textContent).toBe("Menu");
  expect(mobileMenu?.querySelector('a[href="/applications"]')?.textContent).toBe("Applications");
  expect(document.querySelector("aside.md\\:flex nav a")?.getAttribute("href")).toBe("/applications");
});
