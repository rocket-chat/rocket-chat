import { test, expect } from "@playwright/test";

const API = "http://localhost:8000";

test.describe("Settings: one save bar, search and grouped fields", () => {
  test.afterEach(async ({ request }) => {
    await request.delete(`${API}/v1/settings/user_preferences`);
    await request.put(`${API}/v1/admin/settings/budgets`, { data: { config: {}, locked_keys: [] } });
  });

  test("edits across several sections are saved or discarded together", async ({ page }) => {
    await page.goto("/settings/user/preferences");
    const suggest = page.getByTestId("settings-field-user_preferences-suggest_next_questions");
    const notify = page.getByTestId("settings-field-notify-user_preferences-turn_notifications");
    await expect(suggest).toBeEnabled();
    await expect(notify).toBeEnabled();
    await expect(page.getByTestId("settings-save-bar")).toBeHidden();
    const [suggestBefore, notifyBefore] = [await suggest.isChecked(), await notify.isChecked()];

    await suggest.setChecked(!suggestBefore);
    await notify.setChecked(!notifyBefore);

    await expect(page.getByTestId("settings-unsaved")).toContainText("2 unsaved changes");
    await expect(page.getByTestId("settings-unsaved")).toContainText("in 2 sections");
    // One button saves the whole page: no section has a Save of its own.
    await expect(page.getByRole("button", { name: /^save$/i })).toHaveCount(0);
    await expect(page.getByTestId("settings-save-all")).toHaveCount(1);

    await page.getByTestId("settings-discard-all").click();
    await expect(suggest).toBeChecked({ checked: suggestBefore });
    await expect(page.getByTestId("settings-save-bar")).toBeHidden();

    await suggest.setChecked(!suggestBefore);
    await notify.setChecked(!notifyBefore);
    await page.getByTestId("settings-save-all").click();
    await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();

    await page.reload();
    await expect(suggest).toBeChecked({ checked: !suggestBefore });
    await expect(notify).toBeChecked({ checked: !notifyBefore });
  });

  test("a number shows what it counts and a changed value can go back to its default", async ({ page }) => {
    await page.goto("/settings/org/spend");
    const field = page.getByTestId("settings-field-budgets-max_turn_cost_usd");
    await expect(field).toBeEnabled();
    await expect(field.locator("xpath=..")).toContainText("USD");

    await field.fill("99");
    await page.getByTestId("settings-default-budgets-max_turn_cost_usd").click();
    await expect(field).toHaveValue("15");
  });

  test("a setting is found by what it does and the page scrolls to it", async ({ page }) => {
    await page.goto("/settings/user/profile");
    await page.getByTestId("settings-search-open").click();
    await page.getByTestId("settings-search-input").fill("max_turn_cost");

    await expect(page.getByTestId("settings-search-dialog")).toContainText("Spend");
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/settings\/org\/spend#setting-budgets-max_turn_cost_usd/);
    await expect(page.getByTestId("settings-field-budgets-max_turn_cost_usd")).toBeVisible();
  });

  test("the navigation is grouped by who a page is for", async ({ page }) => {
    await page.goto("/settings/user/profile");
    for (const group of ["Personal", "Integrations", "Organization"]) {
      await expect(page.getByRole("navigation", { name: group })).toBeVisible();
    }
    const personal = page.getByRole("navigation", { name: "Personal" });
    for (const name of ["Profile", "Preferences", "My keys", "My agents"]) {
      await expect(personal.getByRole("link", { name, exact: true })).toBeVisible();
    }
    const organization = page.getByRole("navigation", { name: "Organization" });
    for (const name of ["Members & roles", "Models & credentials", "Spend", "Agents", "Git & GitHub"]) {
      await expect(organization.getByRole("link", { name: new RegExp(`^${name}`) })).toBeVisible();
    }
  });
});
