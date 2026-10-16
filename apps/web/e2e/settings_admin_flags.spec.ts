import { test, expect } from "@playwright/test";

/** Administrators see which settings are theirs alone: they apply to every member. */
test("admin-only settings are flagged for administrators too", async ({ page }) => {
  await page.goto("/settings/github");

  const nav = page.getByRole("link", { name: /Users & Roles/ });
  await expect(nav.getByTestId("nav-admin-only")).toHaveText("ADMIN");
  await expect(page.getByRole("link", { name: /Profile & Account/ }).getByTestId("nav-admin-only")).toHaveCount(0);

  await expect(page.getByText("Admin only · whole organization").first()).toBeVisible();
  // Signing with a member's own key is an organization choice; the member section says where.
  await expect(page.getByTestId("git-signing-mode-note")).toContainText("gpg_key");
});
