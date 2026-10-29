import { test, expect } from "@playwright/test";

const BACKEND = "http://localhost:8000";

/** A member makes the organization's git policy their own, and gives it back. */
test.describe("Personal git policy", () => {
  test.afterEach(async ({ request }) => {
    await request.delete(`${BACKEND}/v1/settings/github`);
  });

  test("a member overrides authorship, then follows the organization again", async ({ page }) => {
    await page.goto("/settings/github");

    const badge = page.getByTestId("settings-inheritance-mine-github-commit_authorship_policy");
    await expect(badge).toContainText("Org Default");

    await page.getByTestId("settings-field-mine-github-commit_authorship_policy").selectOption("user_only");
    await page.getByTestId("settings-save-all").click();
    await expect(badge).toContainText("Overridden");

    await badge.getByRole("button", { name: /reset/i }).click();
    await expect(badge).toContainText("Org Default");
    await expect(page.getByText("follows the organization again")).toBeVisible();
  });
});
