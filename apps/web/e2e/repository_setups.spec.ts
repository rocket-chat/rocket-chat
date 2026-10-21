import { test, expect } from "@playwright/test";

/** An administrator says how to prepare the folders of a repository; the list shows and edits it. */
test.describe("Repository setups", () => {
  test("a setup is added, listed, edited and deleted", async ({ page }) => {
    await page.goto("/settings/org/sandboxes");
    const panel = page.getByTestId("repository-setups");
    await expect(panel).toBeVisible();

    await page.getByTestId("setup-repository").fill("acme/e2e-services");
    await page.getByTestId("setup-folder").fill("*");
    await page.getByTestId("setup-command").fill("go mod download\nmake base");
    await page.getByTestId("setup-save").click();

    const row = panel.getByTestId("repository-setup").filter({ hasText: "acme/e2e-services / *" });
    await expect(row).toContainText("make base");

    await row.getByRole("button", { name: "Edit" }).click();
    await page.getByTestId("setup-command").fill("make base && make mocks");
    await page.getByTestId("setup-save").click();
    await expect(row).toContainText("make mocks");

    await row.getByRole("button", { name: "Delete the setup of acme/e2e-services" }).click();
    await expect(row).toHaveCount(0);
  });
});
