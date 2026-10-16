import { test, expect } from "@playwright/test";

/**
 * An administrator configures Slack bots, response modes, and the router in Settings -> Slack.
 */
test("administrator views slack bots, opens editor and configures routing", async ({ page }) => {
  await page.goto("/settings/slack");

  await expect(page.getByRole("heading", { name: /Slack & chat ops/i })).toBeVisible();
  await expect(page.getByText("Configured Slack Bots")).toBeVisible();

  await page.getByTestId("new-bot-button").click();
  await expect(page.getByTestId("slack-bot-editor")).toBeVisible();
  await expect(page.getByRole("heading", { name: "New Slack Bot" })).toBeVisible();

  // Connection Tab fields
  await page.getByTestId("bot-name-input").fill("Playwright Test Bot");
  await page.getByTestId("bot-token-input").fill("xoxb-dummy-test-token");
  await page.getByTestId("app-token-input").fill("xapp-dummy-test-token");

  // Behaviour Tab
  await page.getByRole("tab", { name: "Behaviour" }).click();
  await page.getByTestId("bot-response-mode-select").selectOption("private");
  await expect(page.getByText("Acknowledgement Reaction")).toBeVisible();

  // Brain Tab
  await page.getByRole("tab", { name: "Brain" }).click();
  await page.getByRole("button", { name: /^Router An AI classifier/ }).click();

  // Choosing a router adds its tab.
  await page.getByRole("tab", { name: "Router" }).click();
  await expect(page.getByTestId("classifier-agent-select")).toBeVisible();
  await expect(page.getByText("Routing Destinations")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add Route" })).toBeVisible();

  // Close editor
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByTestId("slack-bot-editor")).toHaveCount(0);
});
