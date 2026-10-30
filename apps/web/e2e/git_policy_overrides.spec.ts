import { test, expect } from "@playwright/test";

const BACKEND = "http://localhost:8000";

/** A member makes the organization's git policy their own, and gives it back. */
test.describe("Personal git policy", () => {
  test.afterEach(async ({ request }) => {
    await request.delete(`${BACKEND}/v1/settings/github`);
  });

  test("a member chooses who publishes, then follows the organization again", async ({ page }) => {
    await page.goto("/settings/github");

    const badge = page.getByTestId("settings-inheritance-mine-github-publish_as");
    const provenance = badge.locator("[data-provenance]");
    await expect(provenance).not.toHaveAttribute("data-provenance", "member");

    await page.getByTestId("settings-field-mine-github-publish_as").selectOption("bot");
    await page.getByTestId("settings-save-all").click();
    await expect(provenance).toHaveAttribute("data-provenance", "member");
    await expect(badge).toContainText("Yours");

    await badge.getByRole("button", { name: "Use the organization's value" }).click();
    await expect(provenance).not.toHaveAttribute("data-provenance", "member");
    await expect(page.getByText("follows the organization again")).toBeVisible();
  });
});
