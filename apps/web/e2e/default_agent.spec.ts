import { test, expect, type Page, type Route } from "@playwright/test";

// The member's resolved default agent is served by the page's own route: every spec shares one
// dev identity, and a stored default would start other specs' sessions with another agent.
interface Level {
  agent_id: string | null;
  agent_name: string;
  source: string;
  skipped: string[];
}

function defaultAgent(level: Level, inherited: Level, own: string | null = null) {
  return { ...level, team_id: null, locked: false, own_agent_id: own, inherited };
}

const PLATFORM: Level = { agent_id: null, agent_name: "", source: "platform", skipped: [] };
const SECURITY: Level = { agent_id: "persona-security", agent_name: "Security Auditor", source: "org", skipped: [] };

async function serveDefault(page: Page, body: () => unknown) {
  await page.route("**/v1/me/default-agent", (route: Route) => route.fulfill({ json: body() }));
}

test.describe("default agent", () => {
  test("a new session starts with the member's default agent", async ({ page }) => {
    await serveDefault(page, () => defaultAgent(SECURITY, SECURITY));
    await page.goto("/");
    await expect(page.getByTestId("agent-model-picker")).toContainText("Security Auditor");
  });

  test("personal settings show the inherited default, then the member's own", async ({ page }) => {
    let own: string | null = null;
    await serveDefault(page, () =>
      own
        ? defaultAgent({ agent_id: own, agent_name: "General Software Engineer", source: "member", skipped: [] }, SECURITY, own)
        : defaultAgent(SECURITY, SECURITY, null),
    );
    const saved: unknown[] = [];
    await page.route("**/v1/settings/user_preferences", async (route) => {
      if (route.request().method() !== "PUT") return route.fallback();
      saved.push(route.request().postDataJSON());
      own = "persona-general";
      return route.fulfill({ json: {} });
    });

    await page.goto("/settings/user/preferences");
    const panel = page.getByTestId("default-agent-panel");
    const picker = panel.getByLabel("Default agent");
    await expect(panel.getByTestId("default-agent-source")).toHaveText("From organization");
    await expect(panel.getByTestId("default-agent-effective")).toContainText("Security Auditor, from the organization");
    await expect(picker.locator("option").first()).toHaveText("Follow the organization: Security Auditor");
    await expect(picker).toBeEnabled();

    await picker.selectOption("persona-general");
    await expect(page.getByTestId("settings-unsaved")).toContainText("1 unsaved change");
    await page.getByTestId("settings-save-all").click();
    await expect.poll(() => saved).toEqual([{ overrides: { default_agent: "persona-general" } }]);
    await expect(panel.getByTestId("default-agent-source")).toHaveText("Yours");
  });

  test("a level whose agent is gone is named as skipped", async ({ page }) => {
    await serveDefault(page, () => defaultAgent({ ...PLATFORM, skipped: ["org"] }, { ...PLATFORM, skipped: ["org"] }));
    await page.goto("/settings/user/preferences");
    await expect(page.getByTestId("default-agent-skipped")).toContainText("Skipped the organization");
    await expect(page.getByTestId("default-agent-effective")).toContainText("No default applies");
  });

  test("the organization's member defaults pick the default agent from the agents", async ({ page }) => {
    await page.goto("/settings/org");
    const picker = page.getByTestId("settings-field-user_preferences-default_agent");
    await expect(picker.locator("option").first()).toHaveText("None: the platform's default");
    await expect(picker.locator("option", { hasText: "Security Auditor" })).toHaveCount(1);
  });
});
