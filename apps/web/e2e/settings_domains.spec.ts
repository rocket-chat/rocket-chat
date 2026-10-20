import { test, expect, type APIRequestContext } from "@playwright/test";

const BACKEND = "http://localhost:8000";

async function resetOrgSettings(request: APIRequestContext) {
  await request.put(`${BACKEND}/v1/admin/settings/user_preferences`, {
    data: { config: {}, locked_keys: [] },
  });
  await request.put(`${BACKEND}/v1/admin/settings/models`, { data: { config: {}, locked_keys: [] } });
  await request.put(`${BACKEND}/v1/admin/settings/sandboxes`, { data: { config: {}, locked_keys: [] } });
}

test.describe("Schema-driven settings forms", () => {
  test.afterEach(async ({ request }) => {
    await resetOrgSettings(request);
  });

  test("an administrator saves model policy and it persists across a reload", async ({ page }) => {
    await page.goto("/settings/org/models");
    await page.getByTestId("settings-advanced-models").click();
    const temperature = page.getByTestId("settings-field-models-temperature");
    await expect(temperature).toBeVisible();

    await temperature.fill("0.5");
    await page.getByTestId("settings-save-all").click();
    await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();

    await page.reload();
    await page.getByTestId("settings-advanced-models").click();
    await expect(page.getByTestId("settings-field-models-temperature")).toHaveValue("0.5");
  });

  test("server-side validation errors are shown on the offending field", async ({ page }) => {
    await page.goto("/settings/org/models");
    await page.getByTestId("settings-advanced-models").click();
    await page.getByTestId("settings-field-models-max_tokens").fill("0");
    await page.getByTestId("settings-save-all").click();
    await expect(page.getByText(/must be at least 1/)).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "rejected" })).toBeVisible();
  });

  test("sandbox policy is editable from the sandboxes page", async ({ page }) => {
    await page.goto("/settings/org/sandboxes");
    const memory = page.getByTestId("settings-field-sandboxes-memory_limit");
    await memory.fill("6Gi");
    await page.getByTestId("settings-save-all").click();
    await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("settings-field-sandboxes-memory_limit")).toHaveValue("6Gi");
    // The image list moved to the catalogue; the legacy keys no longer have a form field.
    await expect(page.getByTestId("settings-field-sandboxes-default_image")).toHaveCount(0);
  });

  test("a locked member default is shown as locked on the member's page", async ({ page }) => {
    await page.goto("/settings/org");
    const memberDefault = page.getByTestId("settings-field-user_preferences-suggest_next_questions");
    // Unchecking a box that is not checked yet is a no-op that does not wait, and the form
    // shows booleans unchecked until the stored values arrive: wait for them first.
    await expect(memberDefault).toBeEnabled();
    await memberDefault.uncheck();
    await page.getByTestId("settings-lock-user_preferences-suggest_next_questions").check();
    await page.getByTestId("settings-save-all").click();
    await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();

    await page.goto("/settings/user/profile");
    const field = page.getByTestId("settings-field-user_preferences-suggest_next_questions");
    await expect(field).toBeDisabled();
    await expect(field).not.toBeChecked();
    await expect(page.getByText("LOCKED BY ORG")).toBeVisible();
  });
});
