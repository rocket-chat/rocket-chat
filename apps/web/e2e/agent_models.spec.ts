import { test, expect } from "@playwright/test";

// An organization key restricted to agents: the default chain skips it, an agent bound to it uses it.
const RESTRICTED = {
  id: "cred_restricted",
  label: "Claude Platform",
  provider: "anthropic",
  kind: "api_key",
  owner: "org",
  owner_user_id: null,
  fingerprint: "sk-...AAAA",
  api_base: null,
  default_for_provider: false,
  restricted_to_agents: true,
  shared_with_org_agents: [],
  created_at: 0,
};
const MODEL = (id: string, name: string, provider: string) => ({ id, name, provider, context_window: 200000, recommended: false });

test("an agent offers the models of a key restricted to agents, and says when its binding cannot apply", async ({ page }) => {
  await page.route("**/v1/admin/credentials", (route) => route.fulfill({ json: { credentials: [RESTRICTED], shared_by_members: [] } }));
  await page.route("**/v1/models?scope=agents", (route) =>
    route.fulfill({
      json: {
        default_model: "openrouter/deepseek/deepseek-v4.1-flash",
        models: [
          MODEL("openrouter/anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 (OpenRouter)", "Anthropic"),
          MODEL("anthropic/claude-sonnet-5-5", "claude-sonnet-5-5", "Anthropic"),
        ],
      },
    })
  );

  await page.goto("/settings/agents");
  await page.getByTestId("agent-new").click();
  await page.getByTestId("agent-name").fill(`Bound agent ${Date.now()}`);
  await page.getByTestId("agent-section-model").click();

  await page.getByTestId("agent-bind-credential").selectOption("cred_restricted");
  await page.getByTestId("agent-bind").click();
  await expect(page.getByTestId("agent-binding-anthropic")).toContainText("Claude Platform");

  await page.getByTestId("agent-model").click();
  await page.getByRole("option", { name: /Claude Sonnet 5.5 \(OpenRouter\)/ }).click();
  await expect(page.getByTestId("agent-binding-unused")).toContainText("called through openrouter");

  await page.getByTestId("agent-model").click();
  await page.getByRole("option", { name: /^claude-sonnet-5-5/ }).click();
  await expect(page.getByTestId("agent-binding-unused")).toHaveCount(0);

  // A long model id never pushes the editor past its card.
  const panel = await page.locator("#agent-panel-model").boundingBox();
  const editor = await page.getByTestId("agent-editor").boundingBox();
  expect(panel && editor && panel.x + panel.width).toBeLessThanOrEqual((editor?.x ?? 0) + (editor?.width ?? 0) + 1);
});
