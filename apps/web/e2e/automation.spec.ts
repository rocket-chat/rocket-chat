import { test, expect } from "@playwright/test";

test.describe("Automation: triggers and outbound webhooks", () => {
  test.describe.configure({ mode: "serial" });

  test("an administrator adds a review trigger, turns it off and deletes it", async ({ page }) => {
    await page.goto("/settings/org/automation");
    const panel = page.getByTestId("triggers-panel");
    await expect(panel.getByRole("heading", { name: "Triggers" })).toBeVisible();

    const name = `E2E review ${Date.now()}`;
    await panel.getByTestId("trigger-name").fill(name);
    await panel.getByTestId("trigger-source").selectOption("github_pr");
    await panel.getByTestId("trigger-mode").selectOption("review");
    await panel.getByLabel("Repositories").fill("acme/*");
    await panel.getByTestId("trigger-add").click();

    const row = panel.getByTestId("triggers-list").locator("li").filter({ hasText: name });
    await expect(row).toContainText("review");
    await expect(row).toContainText("acme/*");

    await row.getByLabel(`Enable ${name}`).uncheck();
    await expect(row.getByLabel(`Enable ${name}`)).not.toBeChecked();
    await row.getByRole("button", { name: `Delete ${name}` }).click();
    await expect(row).toHaveCount(0);
  });

  test("review mode is only offered for pull request sources", async ({ page }) => {
    await page.goto("/settings/org/automation");
    const panel = page.getByTestId("triggers-panel");
    await panel.getByTestId("trigger-source").selectOption("github_issue_label");
    await expect(panel.getByTestId("trigger-mode")).toHaveCount(0);
    await panel.getByTestId("trigger-source").selectOption("gitlab_mr");
    await expect(panel.getByTestId("trigger-mode")).toBeVisible();
  });

  test("a webhook that is not https is refused with the reason", async ({ page }) => {
    await page.goto("/settings/org/automation");
    const panel = page.getByTestId("webhooks-panel");
    await expect(panel.getByRole("heading", { name: "Outbound webhooks" })).toBeVisible();
    await page.route("**/v1/admin/webhooks", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({ status: 422, json: { detail: "A webhook URL starts with https://." } });
      } else {
        await route.continue();
      }
    });
    await panel.getByTestId("webhook-url").fill("https://hooks.example.com/rocket");
    await panel.getByTestId("webhook-add").click();
    await expect(panel.getByRole("alert")).toContainText("https://");
  });

  test("a new webhook shows its signing secret once", async ({ page }) => {
    await page.route("**/v1/admin/webhooks", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 201,
          json: {
            id: "owh_1",
            url: "https://hooks.example.com/rocket",
            events: ["turn.completed"],
            description: "",
            enabled: true,
            secret_fingerprint: "whs...abcd",
            secret: "whsec_E2E_SHOWN_ONCE",
          },
        });
      } else {
        await route.fulfill({ json: { webhooks: [] } });
      }
    });
    await page.goto("/settings/org/automation");
    const panel = page.getByTestId("webhooks-panel");
    await panel.getByTestId("webhook-url").fill("https://hooks.example.com/rocket");
    await panel.getByTestId("webhook-add").click();
    await expect(panel.getByTestId("webhook-secret")).toContainText("whsec_E2E_SHOWN_ONCE");
  });
});
