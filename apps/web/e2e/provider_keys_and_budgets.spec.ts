import { test, expect } from "@playwright/test";

test.describe("Provider credentials and spend limits", () => {
  test.describe.configure({ mode: "serial" });

  test("a member stores a provider key; only its fingerprint is shown; it can be removed", async ({ page }) => {
    await page.goto("/settings/user/keys");
    const panel = page.getByTestId("credentials-panel-user");
    await expect(panel.getByText("My provider keys")).toBeVisible();

    const label = `E2E member key ${Date.now()}`;
    await panel.getByLabel("Provider").selectOption("anthropic");
    await panel.getByLabel("Label").fill(label);
    await panel.getByTestId("credential-key-user").fill("sk-ant-api03-E2E-SECRET-VALUE-4321");
    await panel.getByTestId("credential-save-user").click();

    const row = panel.locator("li").filter({ hasText: label });
    await expect(row).toContainText("4321");
    await expect(page.getByText("E2E-SECRET-VALUE")).toHaveCount(0);

    await row.getByRole("button", { name: `Delete ${label}` }).click();
    await expect(row).toHaveCount(0);
  });

  test("signing in with OpenRouter goes to the provider and a failed sign-in says why", async ({ page }) => {
    await page.route("**/v1/credentials/oauth/openrouter/start**", (route) =>
      route.fulfill({ json: { authorization_url: "/settings/user/keys?signin=failed&reason=The%20sign-in%20was%20cancelled." } }),
    );
    await page.goto("/settings/user/keys");
    const panel = page.getByTestId("credentials-panel-user");
    await panel.getByRole("button", { name: "Sign in with OpenRouter" }).click();
    await expect(page).toHaveURL(/signin=failed/);
    await expect(page.getByTestId("credentials-panel-user").getByRole("alert")).toContainText("The sign-in was cancelled.");
  });

  test("a key set in the deployment's environment is shown beside the organization's own", async ({ page }) => {
    await page.route("**/v1/admin/credentials/platform", (route) =>
      route.fulfill({ json: [{ provider: "mistral", env_var: "MISTRAL_API_KEY", fingerprint: "sk-...9999" }] }),
    );
    await page.goto("/settings/org/models");
    const panel = page.getByTestId("credentials-panel-org");
    const platformRow = panel.getByTestId("platform-key-mistral");
    await expect(platformRow).toContainText("Set by operator (MISTRAL_API_KEY)");
    await expect(platformRow).toContainText("sk-...9999");

    const label = `E2E restricted key ${Date.now()}`;
    await panel.getByLabel("Label").fill(label);
    await panel.getByTestId("credential-key-org").fill("sk-org-RESTRICTED-VALUE-2468");
    await panel.getByLabel("Only for the agents I bind it to").check();
    await panel.getByTestId("credential-save-org").click();

    const row = panel.locator("li").filter({ hasText: label });
    await expect(row).toContainText("Bound agents only");
    await expect(row).not.toContainText("Default");
    await expect(page.getByText("RESTRICTED-VALUE")).toHaveCount(0);

    await row.getByRole("button", { name: `Delete ${label}` }).click();
    await expect(row).toHaveCount(0);
  });

  test("an administrator adds a spend limit and removes it", async ({ page }) => {
    await page.goto("/settings/org/spend");
    const panel = page.getByTestId("spend-limits-panel");
    const form = panel.getByTestId("spend-limit-form");
    // A warning limit on a single turn, far above what any spec spends: it cannot block other specs.
    await form.getByTestId("spend-limit-period").selectOption("turn");
    await form.getByTestId("spend-limit-amount").fill("4321");
    await form.getByLabel("Mode").selectOption("warn");
    await form.getByTestId("spend-limit-save").click();

    const row = panel.locator("tr").filter({ hasText: "$4321.00" });
    await expect(row).toContainText("Per turn");
    await expect(row).toContainText("warn");

    await row.getByRole("button", { name: /Delete the limit/ }).click();
    await expect(row).toHaveCount(0);
  });
});
