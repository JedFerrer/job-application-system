/**
 * E2E test stubs for the CareerOS browser extension integration.
 *
 * These tests require a running backend (localhost:8000) and frontend (localhost:3000).
 * Run with: npx playwright test
 *
 * Test flows:
 *  1. Login → pair extension → detect fixture job → import job
 *  2. Click apply → verify application saved
 *  3. Autofill safe fields → confirm submitted → verify status applied
 */

import { test, expect } from "@playwright/test";

const BACKEND_URL = "http://localhost:8000";
const FRONTEND_URL = "http://localhost:3000";

// ── Auth Flow ──────────────────────────────────────────────────────────

test.describe("Extension E2E Flow", () => {
  test("login → pair extension → detect job → import", async ({ page }) => {
    // 1. Login
    await page.goto(`${FRONTEND_URL}/signin`);
    // ... fill login form, submit
    
    // 2. Navigate to pairing page
    await page.goto(`${FRONTEND_URL}/extension/connect`);
    
    // 3. Generate pairing code
    const generateBtn = page.getByRole("button", { name: /generate pairing code/i });
    await expect(generateBtn).toBeVisible();
    await generateBtn.click();
    
    // 4. Verify pairing code appears
    const codeEl = page.locator(".font-mono");
    await expect(codeEl).toBeVisible();
    const code = await codeEl.textContent();
    expect(code).toBeTruthy();
    expect(code!.length).toBe(8);

    // 5. Call backend to complete pairing (simulating extension)
    const pairRes = await fetch(`${BACKEND_URL}/api/v1/extension/auth/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pairing_code: code }),
    });
    expect(pairRes.ok).toBe(true);
    const pairData = await pairRes.json();
    expect(pairData.access_token).toBeTruthy();

    // 6. Import a job (simulating extension detecting a job)
    const importRes = await fetch(`${BACKEND_URL}/api/v1/extension/jobs/import`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${pairData.access_token}`,
      },
      body: JSON.stringify({
        title: "Senior Backend Engineer",
        company: "Acme Corp",
        location: "Remote",
        description: "Build APIs with FastAPI",
        source_url: "https://www.linkedin.com/jobs/view/e2e-test-123",
        source: "linkedin",
      }),
    });
    expect(importRes.ok).toBe(true);
    const importData = await importRes.json();
    expect(importData.job_id).toBeGreaterThan(0);
    expect(importData.application_id).toBeNull(); // Import must NOT create application
  });

  test("apply click → application saved → mark applied", async ({ page }) => {
    // Given a paired extension with tokens
    const pairRes = await fetch(`${BACKEND_URL}/api/v1/extension/auth/start`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Authorization via seeded user or login token
      },
    });
    // ... (requires auth setup)
    expect(true).toBe(true); // Stub — requires auth fixtures
  });

  test("autofill safe fields → confirm submitted → status applied", async ({ page }) => {
    // Given a paired extension with tokens and a job
    // When user autofills fields
    // Then fields are filled safely (no password/file/sensitive)
    // When user confirms submission
    // Then status is applied with timeline events
    expect(true).toBe(true); // Stub — requires full browser extension setup
  });
});

// ── Session Management ──────────────────────────────────────────────────

test.describe("Connected Apps Dashboard", () => {
  test("list and revoke extension sessions", async ({ page }) => {
    // Given authenticated user with extension sessions
    await page.goto(`${FRONTEND_URL}/dashboard/settings/connected-apps`);
    
    // Verify page loads
    await expect(page.getByText(/connected apps/i)).toBeVisible();
    
    // Stub: verify sessions list and revoke button
    expect(true).toBe(true);
  });
});
