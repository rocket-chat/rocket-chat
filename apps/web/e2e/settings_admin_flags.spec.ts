import { test, expect } from "@playwright/test";
import { asMember } from "./support/access";

/** Administrators see which settings are theirs alone: they apply to every member. */
test("admin-only settings are flagged for administrators too", async ({ page }) => {
  await page.goto("/settings/github");

  const nav = page.getByRole("link", { name: /Members & roles/ });
  await expect(nav.getByTestId("nav-admin-only")).toHaveText("ADMIN");
  await expect(page.getByRole("link", { name: /^Profile/ }).getByTestId("nav-admin-only")).toHaveCount(0);
  // Every member links their own Slack account there: not an administrator's page.
  await expect(page.getByRole("link", { name: /^Slack/ }).getByTestId("nav-admin-only")).toHaveCount(0);

  // Signing with a member's own key is an organization choice; the member section says where.
  await expect(page.getByTestId("git-signing-mode-note")).toContainText("member_gpg");

  await page.goto("/settings/slack");
  await expect(page.getByText("Admin only · whole organization").first()).toBeVisible();
});

test("a member finds Slack under Integrations, links their account, and sees no bots", async ({ page }) => {
  await asMember(page);
  await page.goto("/settings/user/profile");

  const integrations = page.getByRole("navigation", { name: "Integrations" });
  await expect(integrations.getByRole("link", { name: /^Slack/ })).toBeVisible();
  // Administrators' pages are hidden from members, not dimmed.
  await expect(page.getByRole("link", { name: /Members & roles/ })).toHaveCount(0);

  await integrations.getByRole("link", { name: /^Slack/ }).click();
  await expect(page).toHaveURL(/\/settings\/slack$/);
  await expect(page.getByTestId("slack-link-panel")).toBeVisible();
  await expect(page.getByTestId("slack-bots-admin-only")).toBeVisible();
  await expect(page.getByTestId("new-bot-button")).toHaveCount(0);
  await expect(page.getByTestId("slack-workspace-binding")).toHaveCount(0);
});

/** The organization section header keeps its badge on one line and never overlaps its action. */
test("/settings/knowledge: the organization header fits its badge and action", async ({ page }) => {
  // Narrow enough that the navigation leaves the content column little room.
  await page.setViewportSize({ width: 820, height: 800 });
  await page.goto("/settings/knowledge");

  const badge = page.getByText("Admin only · whole organization").first();
  const button = page.getByRole("button", { name: "New org collection" });
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
