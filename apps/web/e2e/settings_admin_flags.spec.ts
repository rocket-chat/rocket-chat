import { test, expect } from "@playwright/test";

/** Administrators see which settings are theirs alone: they apply to every member. */
test("admin-only settings are flagged for administrators too", async ({ page }) => {
  await page.goto("/settings/github");

  const nav = page.getByRole("link", { name: /Users & roles/ });
  await expect(nav.getByTestId("nav-admin-only")).toHaveText("ADMIN");
  await expect(page.getByRole("link", { name: /Profile & preferences/ }).getByTestId("nav-admin-only")).toHaveCount(0);

  await expect(page.getByText("Admin only · whole organization").first()).toBeVisible();
  // Signing with a member's own key is an organization choice; the member section says where.
  await expect(page.getByTestId("git-signing-mode-note")).toContainText("gpg_key");
});

/** The organization section header keeps its badge on one line and never overlaps its action. */
for (const [path, action] of [
  ["/settings/agents", "New org agent"],
  ["/settings/knowledge", "New org collection"],
] as const) {
  test(`${path}: the organization header fits its badge and action`, async ({ page }) => {
    // Narrow enough that the navigation leaves the content column little room.
    await page.setViewportSize({ width: 820, height: 800 });
    await page.goto(path);

    const badge = page.getByText("Admin only · whole organization").first();
    const button = page.getByRole("button", { name: action });
    await expect(badge).toBeVisible();
    await expect(button).toBeVisible();

    const [badgeBox, buttonBox] = [await badge.boundingBox(), await button.boundingBox()];
    expect(badgeBox!.height).toBeLessThan(30);
    const overlaps =
      badgeBox!.x < buttonBox!.x + buttonBox!.width &&
      buttonBox!.x < badgeBox!.x + badgeBox!.width &&
      badgeBox!.y < buttonBox!.y + buttonBox!.height &&
      buttonBox!.y < badgeBox!.y + badgeBox!.height;
    expect(overlaps).toBe(false);
  });
}
