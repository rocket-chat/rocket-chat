import AxeBuilder from "@axe-core/playwright";
import { test, expect, type Page } from "@playwright/test";

async function expectNoBlockingViolations(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
}

/** A team made through the page, with a name no other spec uses. */
async function createTeam(page: Page, prefix: string): Promise<{ id: string; name: string }> {
  const name = `${prefix} ${Date.now().toString(36)}`;
  await page.goto("/settings/members");
  await page.getByTestId("members-tab-teams").click();
  await page.getByTestId("team-new-name").fill(name);
  const created = page.waitForResponse((res) => res.url().endsWith("/v1/teams") && res.request().method() === "POST");
  await page.getByTestId("team-create").click();
  const response = await created;
  expect(response.status()).toBe(201);
  const { id } = (await response.json()) as { id: string };
  await expect(page.getByTestId("team-detail-name")).toHaveText(name);
  return { id, name };
}

test.describe("settings reorganized by owner", () => {
  test("retired pages land on their new home", async ({ page }) => {
    await page.goto("/settings/user/agents");
    await expect(page).toHaveURL(/\/settings\/agents\?owner=me$/);
    await expect(page.getByTestId("owner-filter-select")).toHaveValue("me");

    await page.goto("/settings/org/users");
    await expect(page).toHaveURL(/\/settings\/members$/);
    await expect(page.getByRole("heading", { level: 1, name: "Members & teams" })).toBeVisible();
  });

  test("the owner filter narrows the navigation, travels in the URL and is remembered", async ({ page }) => {
    await page.goto("/settings/agents");
    const filter = page.getByTestId("owner-filter-select");
    await expect(filter).toHaveValue("all");

    await filter.selectOption("me");
    await expect(page).toHaveURL(/owner=me/);
    // Only pages about the person's own objects remain, and "You" always does.
    await expect(page.getByRole("link", { name: /^Sandboxes/ })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /^My usage/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "Profile", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /^Skills/ })).toHaveAttribute("href", "/settings/skills?owner=me");

    await filter.selectOption("org");
    await expect(page).toHaveURL(/owner=org/);
    await expect(page.getByRole("link", { name: /^Audit log/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /^My usage/ })).toHaveCount(0);

    // A page opened without the parameter gets the remembered choice.
    await page.goto("/settings/skills");
    await expect(page).toHaveURL(/\/settings\/skills\?owner=org/);
    await expect(page.getByRole("heading", { name: "Personal Skills" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Organization Skills" })).toBeVisible();

    await page.getByTestId("owner-filter-select").selectOption("all");
    await expect(page).toHaveURL(/\/settings\/skills$/);
    await expect(page.getByRole("heading", { name: "Personal Skills" })).toBeVisible();
  });

  test("a member of an organization without teams sees no owner filter", async ({ page }) => {
    await page.route("**/v1/me/access", (route) =>
      route.fulfill({
        json: { org_id: "default_org", org_name: "Default organization", role: "member", roles: ["member"], capabilities: ["create_personal_agents"] },
      }),
    );
    await page.route("**/v1/teams", (route) => route.fulfill({ json: { teams: [] } }));
    await page.goto("/settings/agents");
    await expect(page.getByRole("heading", { level: 1, name: "Agents" })).toBeVisible();
    await expect(page.getByTestId("owner-filter")).toHaveCount(0);
  });

  test("an administrator creates a team and adds a member to it", async ({ page }) => {
    const team = await createTeam(page, "Platform");
    // Searching the organization needs its member directory (a database); the reply is fixed here.
    await page.route(`**/v1/teams/${team.id}/candidates**`, (route) =>
      route.fulfill({ json: { members: [{ user_id: "teammate_e2e", email: "teammate@example.com", name: "Teammate", role: "member" }] } }),
    );
    await page.getByTestId("team-candidate-query").fill("team");
    await page.getByTestId("team-candidate-search").click();
    await page.getByTestId("team-candidate-add-teammate_e2e").click();

    const row = page.getByTestId("team-member-teammate_e2e");
    await expect(row).toBeVisible();
    await page.getByTestId("team-member-role-teammate_e2e").selectOption("admin");
    await expect(page.getByTestId("team-status")).toHaveText("Member saved.");
    await expect(page.getByTestId(`team-item-${team.id}`)).toContainText("1 member");

    // The owner filter offers the team, and naming it opens it on this page.
    await page.getByTestId("owner-filter-select").selectOption(`team:${team.id}`);
    await expect(page).toHaveURL(new RegExp(`owner=team%3A${team.id}|owner=team:${team.id}`));
    await expect(page.getByTestId("team-detail-name")).toHaveText(team.name);
  });

  test("an agent is created for a team, then moved to the organization", async ({ page }) => {
    const team = await createTeam(page, "Agents team");
    await page.goto("/settings/agents");
    await page.getByTestId("agent-new").click();
    const name = `Team helper ${Date.now().toString(36)}`;
    await page.getByTestId("agent-name").fill(name);
    await page.getByTestId("agent-owner").selectOption(`team:${team.id}`);

    const saved = page.waitForResponse((res) => res.url().endsWith("/v1/agents") && res.request().method() === "POST");
    await page.getByTestId("agent-save").click();
    const body = (await (await saved).json()) as { scope: string; team_id: string };
    expect(body.scope).toBe("team");
    expect(body.team_id).toBe(team.id);

    const teamGroup = page.getByTestId(`agent-group-team:${team.id}`);
    await expect(teamGroup.getByText(name)).toBeVisible();
    await expect(teamGroup.getByTestId("owner-badge").first()).toHaveText(team.name);

    await page.getByTestId("agent-owner").selectOption("org");
    await page.getByTestId("agent-save").click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toContainText(`the members of the team ${team.name}`);
    await expect(dialog).toContainText("everyone in the organization");
    const moved = page.waitForResponse((res) => res.url().endsWith("/v1/agents") && res.request().method() === "POST");
    await page.getByTestId("confirm-accept").click();
    expect(((await (await moved).json()) as { scope: string }).scope).toBe("org");
    await expect(page.getByTestId("agent-group-org").getByText(name)).toBeVisible();
    await expect(page.getByTestId(`agent-group-team:${team.id}`)).toHaveCount(0);

    // The team owns nothing any more, so it can be deleted.
    await page.goto("/settings/members");
    await page.getByTestId("members-tab-teams").click();
    await page.getByTestId(`team-item-${team.id}`).click();
    await expect(page.getByTestId("team-owned")).toHaveText("Owns nothing yet.");
  });

  test("a team that still owns an object is not deleted, and says why", async ({ page }) => {
    const team = await createTeam(page, "Busy team");
    await page.goto(`/settings/skills?owner=team:${team.id}`);
    await page.getByTestId(`new-team-skill-${team.id}`).click();
    await page.getByLabel("Skill Title *").fill(`Team skill ${Date.now().toString(36)}`);
    await page.getByLabel("Skill Instructions / Prompt Body *").fill("Prefer small pull requests.");
    await expect(page.getByTestId("skill-owner")).toHaveValue(`team:${team.id}`);
    await page.getByRole("button", { name: "Save skill" }).click();
    await expect(page.getByTestId("team-scope-section").getByTestId("owner-badge").first()).toHaveText(team.name);

    await page.goto("/settings/members");
    await page.getByTestId("members-tab-teams").click();
    await page.getByTestId(`team-item-${team.id}`).click();
    await expect(page.getByTestId("team-owned")).toHaveText("Owns 1 skills.");
    await page.getByTestId("team-delete").click();
    await page.getByTestId("confirm-accept").click();
    await expect(page.getByTestId("team-status")).toContainText("still owns 1 skills");
  });
});

test.describe("moving and binding under a team", () => {
  test("a personal knowledge base is moved to a team", async ({ page }) => {
    const team = await createTeam(page, "Knowledge team");
    await page.goto("/settings/knowledge");
    await page.getByRole("button", { name: "New collection", exact: true }).click();
    const name = `Runbooks ${Date.now().toString(36)}`;
    await expect(page.getByTestId("knowledge-owner")).toHaveValue("me");
    await page.getByLabel("Collection Name *").fill(name);
    const created = page.waitForResponse((res) => res.url().endsWith("/v1/knowledge/bases") && res.request().method() === "POST");
    await page.getByTestId("kb-submit").click();
    const { id } = (await (await created).json()) as { id: string };

    await page.getByTestId(`kb-edit-${id}`).click();
    await page.getByTestId("knowledge-owner").selectOption(`team:${team.id}`);
    await page.getByTestId("kb-submit").click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toContainText("only you");
    await expect(dialog).toContainText(`the members of the team ${team.name}`);
    const moved = page.waitForResponse((res) => res.url().endsWith(`/v1/knowledge/bases/${id}`) && res.request().method() === "PATCH");
    await page.getByTestId("confirm-accept").click();
    const body = (await (await moved).json()) as { scope: string; team_id: string };
    expect(body).toMatchObject({ scope: "team", team_id: team.id });

    const section = page.getByTestId("team-scope-section").filter({ hasText: team.name });
    await expect(section.getByText(name)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Personal Collections" }).locator("xpath=ancestor::section").getByText(name)).toHaveCount(0);
  });

  test("a team administrator binds a key shared with the team's agent, never an organization key", async ({ page }) => {
    const team = await createTeam(page, "Binding team");
    const agentResponse = await page.request.post("/v1/agents", {
      data: {
        id: `persona-team-${Date.now().toString(36)}`,
        name: `Team bound ${Date.now().toString(36)}`,
        role_title: "Specialist",
        system_prompt: "Work carefully.",
        model: "",
        scope: "team",
        team_id: team.id,
      },
    });
    expect(agentResponse.status()).toBe(200);
    const agent = (await agentResponse.json()) as { id: string; name: string };
    const key = (id: string, label: string, shared: string[]) => ({
      id, label, provider: "anthropic", kind: "api_key", owner: "user", owner_user_id: "member_user", fingerprint: "ab12",
      api_base: null, default_for_provider: false, restricted_to_agents: false, shared_with_org_agents: shared, created_at: 0,
    });  // prettier-ignore

    // The page as a plain member who administers the team.
    await page.route("**/v1/me/access", (route) =>
      route.fulfill({ json: { org_id: "default_org", org_name: "Default organization", role: "member", roles: ["member"], capabilities: ["bring_own_keys"] } }),
    );
    await page.route("**/v1/teams", (route) =>
      route.fulfill({ json: { teams: [{ ...team, description: "", member_count: 1, my_role: "admin", can_manage: true }] } }),
    );
    // What the API offers this person on the agent (the test stack runs every call as the
    // development administrator, so the member's answer is fixed here).
    await page.route(`**/v1/agents/${agent.id}/bindable-credentials`, (route) =>
      route.fulfill({ json: [key("cred_shared", "Shared with the agent", [agent.id])] }),
    );
    await page.route("**/v1/user/credentials", (route) =>
      route.fulfill({ json: [key("cred_shared", "Shared with the agent", [agent.id]), key("cred_private", "Not shared", [])] }),
    );
    await page.goto(`/settings/agents?owner=team:${team.id}`);
    await page.getByTestId(`agent-item-${agent.id}`).click();
    await page.getByTestId("agent-section-model").click();

    await expect(page.getByTestId("agent-team-binding-note")).toContainText("Organization keys are bound by organization administrators");
    const options = page.getByTestId("agent-bind-credential").locator("option");
    await expect(options).toHaveText(["Choose a credential", "Shared with the agent (anthropic)"]);
    await page.getByTestId("agent-bind-credential").selectOption("cred_shared");
    await page.getByTestId("agent-bind").click();
    await expect(page.getByTestId("agent-binding-anthropic")).toContainText("Shared with the agent");
  });
});

async function createTeamAgent(page: Page, teamId: string): Promise<{ id: string; name: string }> {
  const stamp = Date.now().toString(36);
  const res = await page.request.post("/v1/agents", {
    data: { id: `persona-t-${stamp}`, name: `Team agent ${stamp}`, role_title: "Specialist", system_prompt: "Work carefully.", model: "", scope: "team", team_id: teamId },
  });
  expect(res.status()).toBe(200);
  return (await res.json()) as { id: string; name: string };
}

test.describe("a team's keys and limits", () => {
  test("a team's keys are added, rotated and deleted from Models & keys", async ({ page }) => {
    const team = await createTeam(page, "Keys team");
    await page.goto(`/settings/org/models?owner=team:${team.id}`);
    await expect(page.getByTestId("credentials-panel-org")).toHaveCount(0);
    const panel = page.getByTestId(`credentials-panel-team-${team.id}`);
    await expect(panel.getByRole("heading", { name: `Team keys · ${team.name}` })).toBeVisible();

    await panel.getByLabel("Label").fill("Team Anthropic");
    await panel.getByTestId(`credential-key-team-${team.id}`).fill("sk-ant-team-key-0123456789");
    const created = page.waitForResponse((res) => res.url().endsWith(`/v1/teams/${team.id}/credentials`) && res.request().method() === "POST");
    await panel.getByTestId(`credential-save-team-${team.id}`).click();
    const credential = (await (await created).json()) as { id: string; owner: string; team_id: string };
    expect(credential).toMatchObject({ owner: "team", team_id: team.id });

    const row = panel.getByTestId(`credential-${credential.id}`);
    await expect(row.getByTestId("owner-badge")).toHaveText(team.name);
    await row.getByRole("button", { name: "Rotate Team Anthropic" }).click();
    await row.getByLabel("New key").fill("sk-ant-team-key-rotated-99");
    const rotated = page.waitForResponse((res) => res.url().endsWith(`/credentials/${credential.id}/rotate`));
    await row.getByRole("button", { name: "Rotate", exact: true }).click();
    expect((await rotated).status()).toBe(200);

    await row.getByRole("button", { name: "Delete Team Anthropic" }).click();
    await expect(panel.getByTestId(`credential-${credential.id}`)).toHaveCount(0);

    // The Everything view of an administrator shows the organization's keys and each team's.
    await page.getByTestId("owner-filter-select").selectOption("all");
    await expect(page.getByTestId("credentials-panel-org")).toBeVisible();
    await expect(page.getByTestId(`credentials-panel-team-${team.id}`)).toBeVisible();
  });

  test("a team's limits: its own are changed, the organization's on it are read-only", async ({ page }) => {
    const team = await createTeam(page, "Limits team");
    const agent = await createTeamAgent(page, team.id);

    // The organization limits the team from its own view.
    await page.goto("/settings/org/spend?owner=org");
    const orgForm = page.getByTestId("spend-limits-panel").getByTestId("spend-limit-form");
    await orgForm.getByTestId("spend-limit-subject").selectOption("team");
    await orgForm.getByTestId("spend-limit-team").selectOption(team.id);
    await orgForm.getByTestId("spend-limit-amount").fill("777");
    const orgLimit = page.waitForResponse((res) => res.url().endsWith("/v1/admin/spend-limits") && res.request().method() === "POST");
    await orgForm.getByTestId("spend-limit-save").click();
    const orgOwned = (await (await orgLimit).json()) as { id: string };

    await page.getByTestId("owner-filter-select").selectOption(`team:${team.id}`);
    const panel = page.getByTestId(`spend-limits-panel-team-${team.id}`);
    await expect(page.getByTestId("spend-limits-panel")).toHaveCount(0);
    const orgRow = panel.getByTestId(`spend-limit-${orgOwned.id}`);
    await expect(orgRow.getByTestId("spend-limit-org-note")).toHaveText("Set by the organization");
    await expect(orgRow.getByRole("button", { name: /Delete the limit/ })).toHaveCount(0);
    await expect(orgRow.getByRole("button", { name: "block" })).toBeDisabled();

    // The team limits one of its agents.
    const form = panel.getByTestId("spend-limit-form");
    await expect(form.getByTestId("spend-limit-subject").locator("option")).toHaveText(["The team", "One of its agents", "One of its agents, for each member"]);
    await form.getByTestId("spend-limit-subject").selectOption("agent");
    await form.getByTestId("spend-limit-agent").selectOption(agent.id);
    await form.getByTestId("spend-limit-amount").fill("55");
    const teamLimit = page.waitForResponse((res) => res.url().endsWith(`/v1/teams/${team.id}/spend-limits`) && res.request().method() === "POST");
    await form.getByTestId("spend-limit-save").click();
    const created = (await (await teamLimit).json()) as { id: string; team_id: string };
    expect(created.team_id).toBe(team.id);
    const teamRow = panel.getByTestId(`spend-limit-${created.id}`);
    await expect(teamRow).toContainText(agent.name);
    await teamRow.getByRole("button", { name: "block" }).click();
    await expect(teamRow.getByRole("button", { name: "warn" })).toBeVisible();
    await teamRow.getByRole("button", { name: /Delete the limit/ }).click();
    await expect(panel.getByTestId(`spend-limit-${created.id}`)).toHaveCount(0);
  });

  test("the agent editor offers the keys the API says are bindable, the team's included", async ({ page }) => {
    const team = await createTeam(page, "Bindable team");
    const agent = await createTeamAgent(page, team.id);
    const key = await page.request.post(`/v1/teams/${team.id}/credentials`, {
      data: { provider: "anthropic", api_key: "sk-ant-bindable-0123456789", label: "Team bindable key" },
    });
    expect(key.status()).toBe(200);
    const { id: keyId } = (await key.json()) as { id: string };

    const listed = page.waitForResponse((res) => res.url().endsWith(`/v1/agents/${agent.id}/bindable-credentials`));
    await page.goto(`/settings/agents?owner=team:${team.id}`);
    await page.getByTestId(`agent-item-${agent.id}`).click();
    await page.getByTestId("agent-section-model").click();
    const response = await listed;
    expect(response.status(), await response.text()).toBe(200);
    const bindable = (await response.json()) as { id: string }[];
    expect(bindable.map((c) => c.id)).toContain(keyId);

    await page.getByTestId("agent-bind-credential").selectOption(keyId);
    await page.getByTestId("agent-bind").click();
    await expect(page.getByTestId("agent-binding-anthropic")).toContainText("Team bindable key");
    const saved = page.waitForResponse((res) => res.url().endsWith("/v1/agents") && res.request().method() === "POST");
    await page.getByTestId("agent-save").click();
    expect((await saved).status()).toBe(200);
  });
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(`teams pages, WCAG 2.1 AA (${scheme})`, () => {
    test.use({ colorScheme: scheme });
    test("the teams tab and a team's detail have no serious or critical violations", async ({ page }) => {
      await createTeam(page, `A11y ${scheme}`);
      await expectNoBlockingViolations(page);
    });

    test("a team's keys and limits have no serious or critical violations", async ({ page }) => {
      const team = await createTeam(page, `A11y keys ${scheme}`);
      for (const path of [`/settings/org/models?owner=team:${team.id}`, `/settings/org/spend?owner=team:${team.id}`]) {
        await page.goto(path);
        await expect(page.getByTestId(path.includes("models") ? `credentials-panel-team-${team.id}` : `spend-limits-panel-team-${team.id}`)).toBeVisible();
        await expectNoBlockingViolations(page);
      }
    });
  });
}
