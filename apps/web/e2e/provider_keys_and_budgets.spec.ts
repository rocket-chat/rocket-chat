import { test, expect } from "@playwright/test";

test.describe("Provider keys and budgets", () => {
  test.describe.configure({ mode: "serial" });

  test("a member stores a provider key; only its fingerprint is shown; it can be removed", async ({ page }) => {
    await page.goto("/settings/user/models");
    const panel = page.getByTestId("provider-keys-user");
    await expect(panel.getByText("Your provider keys")).toBeVisible();

    await panel.getByLabel("Provider").fill("anthropic");
    await panel.getByLabel("API key").fill("sk-ant-api03-E2E-SECRET-VALUE-4321");
    await panel.getByRole("button", { name: "Save key" }).click();

    await expect(panel.getByText("anthropic key saved")).toBeVisible();
    await expect(panel.getByText("sk-...4321")).toBeVisible();
    await expect(page.getByText("E2E-SECRET-VALUE")).toHaveCount(0);

    await panel.getByRole("button", { name: "Remove anthropic key" }).click();
    await expect(panel.getByText("anthropic key removed")).toBeVisible();
    await expect(panel.getByText("No keys stored.")).toBeVisible();
  });

  test("a key set in the deployment's environment is shown, and the organization can override it", async ({ page }) => {
    await page.route("**/v1/admin/model-keys/platform", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ provider: "mistral", env_var: "MISTRAL_API_KEY", fingerprint: "sk-...9999" }]),
      })
    );
    await page.goto("/settings/org/models");
    const panel = page.getByTestId("provider-keys-org");
    const row = panel.getByTestId("provider-key-mistral");

    await expect(row).toContainText("Deployment key");
    await expect(row).toContainText("MISTRAL_API_KEY");
    await expect(row).toContainText("sk-...9999");

    await row.getByRole("button", { name: "Override the mistral key for this organization" }).click();
    await expect(panel.getByLabel("Provider")).toHaveValue("mistral");
    await expect(panel.getByLabel("API key")).toBeFocused();

    await panel.getByLabel("API key").fill("sk-org-OVERRIDE-VALUE-2468");
    await panel.getByRole("button", { name: "Save key" }).click();
    await expect(row).toContainText("Organization key");
    await expect(row).toContainText("sk-...2468");
    await expect(row).toContainText("Replaces the deployment's key (MISTRAL_API_KEY)");
    await expect(page.getByText("OVERRIDE-VALUE")).toHaveCount(0);

    await panel.getByRole("button", { name: "Remove mistral key" }).click();
    await expect(row).toContainText("Deployment key");
  });

  test("the organization budget and key policy are editable settings", async ({ page }) => {
    await page.goto("/settings/org/models");
    await expect(page.getByText("Budgets & provider keys")).toBeVisible();
    await expect(page.getByTestId("provider-keys-org")).toBeVisible();

    const res = await page.request.put("/v1/admin/budget", { data: { monthly_budget_cap_usd: 75 } });
    expect(res.status()).toBe(200);
    const usage = await (await page.request.get("/v1/settings/org/usage")).json();
    expect(usage.monthly_budget_cap_usd).toBe(75);

    await page.request.put("/v1/admin/budget", { data: { monthly_budget_cap_usd: 0 } });
  });
});
