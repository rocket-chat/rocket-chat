import { test, expect } from "@playwright/test";
import { asMember } from "./support/access";

/**
 * An administrator configures Slack bots, response modes, and routing in Settings -> Slack.
 */
test("administrator views slack bots, opens editor and configures routing", async ({ page }) => {
  await page.goto("/settings/slack");

  await expect(page.getByRole("heading", { name: /Slack & chat ops/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Slack bots" })).toBeVisible();
  // Whether linked members' own requests keep the restricted tools is an organization setting.
  await expect(page.getByText("Restrict Linked Members")).toBeVisible();
  // The default agent is the bot's own now, not an organization setting.
  await expect(page.getByTestId("settings-field-slack-default_agent_persona")).toHaveCount(0);

  await page.getByTestId("new-bot-button").click();
  await expect(page.getByTestId("slack-bot-editor")).toBeVisible();
  await expect(page.getByRole("heading", { name: "New Slack Bot" })).toBeVisible();
  // A new bot has no activity yet.
  await expect(page.getByTestId("bot-tab-activity")).toHaveCount(0);

  // Connection Tab fields
  await page.getByTestId("bot-name-input").fill("Playwright Test Bot");
  await page.getByTestId("bot-token-input").fill("xoxb-dummy-test-token");
  await page.getByTestId("app-token-input").fill("xapp-dummy-test-token");

  // Behaviour Tab
  await page.getByRole("tab", { name: "Behaviour" }).click();
  await page.getByTestId("bot-response-mode-select").selectOption("private");
  await expect(page.getByText("Acknowledgement Reaction")).toBeVisible();
  // Private requests can report by direct message instead of messages a reload removes.
  const dmUpdates = page.getByTestId("private-dm-updates");
  await expect(dmUpdates).not.toBeChecked();
  await dmUpdates.check();
  await expect(dmUpdates).toBeChecked();

  // Routing Tab: one table, the default row first; intents bring the classifier's settings.
  await page.getByRole("tab", { name: "Routing" }).click();
  await expect(page.getByTestId("routing-table")).toBeVisible();
  await expect(page.getByTestId("routing-row-0")).toContainText("Default");
  await expect(page.getByTestId("routing-remove-0")).toHaveCount(0);
  await expect(page.getByTestId("classifier-settings")).toHaveCount(0);
  await page.getByTestId("routing-add-intent").click();
  await expect(page.getByTestId("classifier-agent-select")).toBeVisible();
  await page.getByTestId("routing-remove-1").click();
  await expect(page.getByTestId("classifier-settings")).toHaveCount(0);

  // Close editor
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByTestId("slack-bot-editor")).toHaveCount(0);
});

test("the routing table maps channels and intents to an agent, a repository and a branch", async ({ page }) => {
  let saved: Record<string, unknown> | null = null;
  await page.route("**/v1/admin/slack/bots", async (route) => {
    if (route.request().method() === "POST") {
      saved = route.request().postDataJSON();
      return route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "stop" }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/settings/slack");
  await page.getByTestId("new-bot-button").click();
  await page.getByTestId("bot-name-input").fill("Routing Bot");
  await page.getByTestId("bot-token-input").fill("xoxb-dummy");
  await page.getByTestId("app-token-input").fill("xapp-dummy");
  await page.getByTestId("bot-tab-routing").click();

  const defaultAgent = await page.getByTestId("routing-agent-0").inputValue();

  await page.getByTestId("routing-add-channel").click();
  await page.getByTestId("routing-match-1").fill("C0123ABCD");
  await page.getByTestId("routing-repository-1").fill("acme/api");
  await page.getByTestId("routing-branch-1").fill("main");

  await page.getByTestId("routing-add-intent").click();
  await page.getByTestId("routing-match-2").fill("bug_report");
  await page.getByTestId("routing-repository-2").fill("acme/web");
  await page.getByTestId("routing-details-2").click();
  await page.getByLabel("When to choose it").fill("The person reports a bug");

  // A channel listed twice cannot be saved, and the editor says why.
  await page.getByTestId("routing-add-channel").click();
  await page.getByTestId("routing-match-3").fill("C0123ABCD");
  await page.getByTestId("save-bot-button").click();
  await expect(page.getByTestId("bot-editor-error")).toContainText("'C0123ABCD' appears twice");
  expect(saved).toBeNull();
  await page.getByTestId("routing-remove-3").click();

  await page.getByTestId("save-bot-button").click();
  await expect.poll(() => saved).not.toBeNull();
  const body = saved as unknown as {
    agent_id: string;
    policy: Record<string, unknown>;
    router: { classifier_agent_id: string; routes: Record<string, unknown>[] };
  };
  expect(body.agent_id).toBe(defaultAgent);
  expect(body.policy).toEqual({ channel_profiles: { C0123ABCD: { repository: "acme/api", branch: "main" } } });
  expect(body.router.classifier_agent_id).toBe(defaultAgent);
  expect(body.router.routes).toEqual([
    expect.objectContaining({ name: "bug_report", description: "The person reports a bug", repository: "acme/web" }),
  ]);
});

test("a member sees that Slack bots are managed by administrators", async ({ page }) => {
  await asMember(page);
  await page.goto("/settings/slack");

  await expect(page.getByTestId("slack-bots-admin-only")).toBeVisible();
  await expect(page.getByTestId("new-bot-button")).toHaveCount(0);
  await expect(page.getByTestId("slack-workspace-binding")).toHaveCount(0);
  await expect(page.getByTestId("slack-link-panel")).toBeVisible();
});
