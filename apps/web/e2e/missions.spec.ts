import { test, expect } from "@playwright/test";

test.describe("Scheduled missions", () => {
  test("schedule, run now, pause and delete a mission", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sidebar-missions-link").click();
    await expect(page).toHaveURL(/\/missions/);

    await page.getByTestId("mission-new").click();
    await page.getByTestId("mission-name").fill("E2E nightly triage");
    await page.getByTestId("mission-prompt").fill("Triage new issues");
    await page.getByTestId("mission-cron").selectOption("0 3 * * *");
    await page.getByTestId("mission-create").click();

    const item = page.getByTestId("mission-item").filter({ hasText: "E2E nightly triage" });
    await expect(item).toBeVisible();
    await expect(item).toContainText("Every night at 03:00");

    await item.getByTestId("mission-run").click();
    await expect(item.getByRole("link", { name: "open last run" })).toBeVisible();

    await item.getByTestId("mission-toggle").click();
    await expect(item).toContainText("paused");

    await item.getByRole("button", { name: /Delete/ }).click();
    await expect(item).toHaveCount(0);
  });

  test("an unsafe schedule is explained, not silently accepted", async ({ page, request }) => {
    const res = await request.post("http://localhost:8000/v1/missions", {
      data: { name: "x", prompt: "y", cron: "* * * * *" },
    });
    expect(res.status()).toBe(422);
    expect((await res.json()).detail).toContain("5 minutes");
    await page.goto("/missions");
    await page.getByTestId("mission-new").click();
    await expect(page.getByTestId("mission-form")).toBeVisible();
  });
});

test.describe("Session templates", () => {
  test("save a template and start a configured session from it", async ({ page }) => {
    await page.goto("/missions");
    await page.getByTestId("template-new").click();
    await page.getByTestId("template-name").fill("E2E bug hunt");
    await page.getByTestId("template-prompt").fill("Hunt for the flaky test");
    await page.getByTestId("template-create").click();

    const item = page.getByTestId("template-item").filter({ hasText: "E2E bug hunt" });
    await expect(item).toBeVisible();
    await item.getByTestId("template-start").click();

    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    await expect(page.getByText("Hunt for the flaky test").first()).toBeVisible({ timeout: 15000 });

    await page.goto("/missions");
    await page
      .getByTestId("template-item")
      .filter({ hasText: "E2E bug hunt" })
      .getByRole("button", { name: /Delete template/ })
      .click();
    await expect(page.getByTestId("template-item").filter({ hasText: "E2E bug hunt" })).toHaveCount(0);
  });
});
