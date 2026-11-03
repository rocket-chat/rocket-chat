import { test, expect } from "@playwright/test";

test.describe("Automation: outbound webhooks", () => {
  test.describe.configure({ mode: "serial" });

  test("the Automation page keeps only outbound webhooks and points to Missions", async ({ page }) => {
    await page.goto("/settings/org/automation");
    await expect(page.getByTestId("triggers-panel")).toHaveCount(0);
    await expect(page.getByTestId("webhooks-panel")).toBeVisible();
    const note = page.getByTestId("automation-missions-note");
    await expect(note).toContainText("is now a mission");
    await note.getByRole("link", { name: "Open Missions" }).click();
    await expect(page).toHaveURL(/\/missions$/);
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
