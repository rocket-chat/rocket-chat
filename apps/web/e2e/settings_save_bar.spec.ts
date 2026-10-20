import { test, expect } from "@playwright/test";

const API = "http://localhost:8000";

test.describe("Settings: one save bar, search and grouped fields", () => {
  test.afterEach(async ({ request }) => {
    for (const domain of ["models", "budgets", "routing"]) {
      await request.put(`${API}/v1/admin/settings/${domain}`, { data: { config: {}, locked_keys: [] } });
    }
  });

  test("edits across several sections are saved or discarded together", async ({ page }) => {
    await page.goto("/settings/org/models");
    await page.getByTestId("settings-advanced-models").click();
    await expect(page.getByTestId("settings-save-bar")).toBeHidden();

    await page.getByTestId("settings-field-models-temperature").fill("0.7");
    await page.getByTestId("settings-field-budgets-monthly_budget_usd").fill("250");

    await expect(page.getByTestId("settings-unsaved")).toContainText("2 unsaved changes");
    await expect(page.getByTestId("settings-unsaved")).toContainText("in 2 sections");
    // One button saves the whole page: no section has a Save of its own.
    await expect(page.getByRole("button", { name: /^save$/i })).toHaveCount(0);
    await expect(page.getByTestId("settings-save-all")).toHaveCount(1);

    await page.getByTestId("settings-discard-all").click();
    await expect(page.getByTestId("settings-field-models-temperature")).toHaveValue("0.2");
    await expect(page.getByTestId("settings-save-bar")).toBeHidden();

    await page.getByTestId("settings-field-models-temperature").fill("0.7");
    await page.getByTestId("settings-field-budgets-monthly_budget_usd").fill("250");
    await page.getByTestId("settings-save-all").click();
    await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("settings-field-budgets-monthly_budget_usd")).toHaveValue("250");
  });

  test("a number shows what it counts and a changed value can go back to its default", async ({ page }) => {
    await page.goto("/settings/org/models");
    const field = page.getByTestId("settings-field-budgets-monthly_budget_usd");
    await expect(field.locator("xpath=..")).toContainText("USD");

    await field.fill("99");
    await page.getByTestId("settings-default-budgets-monthly_budget_usd").click();
    await expect(field).toHaveValue("0");
  });

  test("a setting is found by what it does and the page scrolls to it", async ({ page }) => {
    await page.goto("/settings/user/profile");
    await page.getByTestId("settings-search-open").click();
    await page.getByTestId("settings-search-input").fill("monthly spend");

    await expect(page.getByTestId("settings-search-dialog")).toContainText("Monthly budget");
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/settings\/org\/models#setting-budgets-monthly_budget_usd/);
    await expect(page.getByTestId("settings-field-budgets-monthly_budget_usd")).toBeVisible();
  });

  test("the navigation is grouped by who a page is for", async ({ page }) => {
    await page.goto("/settings/user/profile");
    for (const group of ["Personal", "Agent capabilities", "Organization"]) {
      await expect(page.getByRole("navigation", { name: group })).toBeVisible();
    }
    await expect(page.getByRole("navigation", { name: "Personal" }).getByRole("link", { name: /Profile & preferences/ })).toBeVisible();
  });
});
