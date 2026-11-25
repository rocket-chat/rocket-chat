import { test, expect, type Page, type Route } from "@playwright/test";

const API = "http://localhost:8000";

const SOURCE = {
  id: "src_e2e",
  name: "Agent skills",
  repository: "acme/agent-skills",
  ref: "main",
  auth: "app",
  scope: "org",
  user_id: null,
  team_id: null,
  status: "ok",
  last_commit: "abcdef1234567890",
  last_synced_at: 1_790_000_000,
  last_error: "",
  url: "https://github.com/acme/agent-skills",
  has_manifest: true,
  plugin_count: 2,
  skill_count: 3,
  imported_count: 0,
  can_manage: true,
  notes: [],
};

function entry(path: string, name: string, plugins: string[]) {
  return {
    path,
    name,
    description: `${name} description`,
    plugins,
    files: [],
    problem: "",
    url: `https://github.com/acme/agent-skills/tree/main/${path}`,
    imported: [],
  };
}

const CATALOGUE = {
  source: SOURCE,
  commit: "abcdef1234567890",
  has_manifest: true,
  plugins: [
    { name: "data", description: "Data team", entries: ["teams/data/bq-envs", "teams/data/pr-ci"] },
    { name: "backend", description: "", entries: ["teams/backend/signoz"] },
  ],
  entries: [
    entry("teams/backend/signoz", "signoz", ["backend"]),
    entry("teams/data/bq-envs", "bq-envs", ["data"]),
    entry("teams/data/pr-ci", "pr-ci", ["data"]),
  ],
  notes: [],
};

const SYNCED_SKILL = {
  id: "skill-synced",
  name: "signoz",
  title: "signoz",
  description: "Query SigNoz",
  body: "Old steps.",
  scope: "user",
  user_id: "dev-user",
  team_id: null,
  is_active: true,
  created_at: 0,
  updated_at: 0,
  source: {
    source_id: "src_e2e",
    name: "Agent skills",
    repository: "acme/agent-skills",
    ref: "main",
    path: "teams/backend/signoz",
    plugin: "backend",
    commit: "abcdef1",
    status: "synced",
    problem: "",
    url: "https://github.com/acme/agent-skills/tree/main/teams/backend/signoz",
    files: [],
  },
};

const MISSING_SKILL = {
  ...SYNCED_SKILL,
  id: "skill-missing",
  name: "old-skill",
  title: "old-skill",
  is_active: false,
  source: { ...SYNCED_SKILL.source, path: "teams/old", status: "missing", problem: "gone" },
};

async function mockSources(page: Page, onImport: (body: Record<string, unknown>) => unknown) {
  await page.route("**/v1/external-sources", (route: Route) => route.fulfill({ json: [SOURCE] }));
  await page.route("**/v1/external-sources/src_e2e/catalogue", (route: Route) => route.fulfill({ json: CATALOGUE }));
  await page.route("**/v1/external-sources/src_e2e/import", async (route: Route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: onImport(body) });
  });
}

test.describe("External sources", () => {
  test("connecting a repository the platform cannot read shows why, and it can be disconnected", async ({ page, request }) => {
    const repository = `acme/e2e-${Date.now()}`;
    await page.goto("/settings/sources");
    await page.getByTestId("source-connect").click();
    const form = page.getByTestId("source-connect-form");
    await form.getByTestId("source-repository").fill(repository);
    await form.getByTestId("source-connect-submit").click();

    const card = page.getByTestId("source-card").filter({ hasText: repository });
    await expect(card.getByTestId("source-status")).toHaveText("Read failed");
    await expect(card.getByTestId("source-error")).toContainText("GitHub App");

    await card.getByRole("button", { name: /disconnect/i }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Disconnect", exact: true }).click();
    await expect(card).toHaveCount(0);
    const left = (await (await request.get(`${API}/v1/external-sources`)).json()) as { repository: string }[];
    expect(left.some((s) => s.repository === repository)).toBe(false);
  });

  test("a whole plugin is imported from the skill editor, and a conflict is merged on request", async ({ page }) => {
    const calls: Record<string, unknown>[] = [];
    await mockSources(page, (body) => {
      calls.push(body);
      const merge = (body.merge as string[]) ?? [];
      if (!merge.includes("teams/data/pr-ci")) {
        return { imported: [], conflicts: [{ path: "teams/data/pr-ci", name: "pr-ci", skill_id: "skill-local" }] };
      }
      return { imported: [{ id: "a", name: "bq-envs" }, { id: "b", name: "pr-ci" }], conflicts: [] };
    });
    await page.goto("/settings/skills");
    await page.getByRole("button", { name: /new (personal )?skill/i }).first().click();
    await page.getByTestId("skill-mode-source").click();
    await page.getByTestId("source-import-select").selectOption("src_e2e");

    await page.getByTestId("source-group-check-plugin:data").check();
    await expect(page.getByTestId("source-import-submit")).toHaveText("Import 2 skills");
    await page.getByTestId("source-import-submit").click();

    await expect(page.getByTestId("source-import-conflicts")).toContainText("pr-ci");
    await expect(page.getByTestId("source-import-submit")).toBeDisabled();
    await page.getByTestId("source-merge-teams/data/pr-ci").check();
    await page.getByTestId("source-import-submit").click();

    await expect(page.getByText("Imported 2 skills")).toBeVisible();
    expect(calls[0]).toMatchObject({ paths: ["teams/data/bq-envs", "teams/data/pr-ci"], scope: "user", merge: [] });
    expect(calls[1]).toMatchObject({ merge: ["teams/data/pr-ci"] });
  });

  test("a synchronised skill shows its source, and editing it opens one pull request however often it is clicked", async ({ page }) => {
    let proposed: Record<string, unknown> | null = null;
    let requests = 0;
    let answer: () => void = () => {};
    const answered = new Promise<void>((resolve) => (answer = resolve));
    await page.route("**/v1/skills", async (route: Route) => {
      if (route.request().method() === "GET") await route.fulfill({ json: [SYNCED_SKILL, MISSING_SKILL] });
      else await route.continue();
    });
    await page.route("**/v1/skills/skill-synced/change-request", async (route: Route) => {
      requests += 1;
      proposed = route.request().postDataJSON() as Record<string, unknown>;
      await answered;
      await route.fulfill({ json: { repository: "acme/agent-skills", number: 42, url: "https://github.com/acme/agent-skills/pull/42" } });
    });
    await page.goto("/settings/skills");

    const badges = page.getByTestId("skill-source-badge");
    await expect(badges.first()).toContainText("Agent skills · backend");
    await expect(page.getByTestId("skill-source-notice")).toContainText("no longer in acme/agent-skills");

    await page.getByRole("button", { name: "Propose a change to signoz" }).click();
    const form = page.getByTestId("skill-form");
    await expect(form).toContainText("PROPOSE A CHANGE");
    await expect(form.getByTestId("skill-source-edit-note")).toContainText("pull request");
    await form.getByLabel("Skill Instructions / Prompt Body *").fill("New steps.");
    const save = form.getByTestId("skill-save");
    await save.dblclick();

    // While GitHub answers, the button says so and takes no other click.
    await expect(save).toBeDisabled();
    await expect(save).toHaveText("Opening pull request…");
    await save.click({ force: true });
    answer();

    await expect(page.getByTestId("skill-change-opened")).toContainText("#42");
    expect(proposed).toMatchObject({ title: "signoz", body: "New steps." });
    expect(requests).toBe(1);
  });

  test("without a connected source the editor says where to connect one", async ({ page }) => {
    await page.route("**/v1/external-sources", (route: Route) => route.fulfill({ json: [] }));
    await page.goto("/settings/skills");
    await page.getByRole("button", { name: /new (personal )?skill/i }).first().click();
    await page.getByTestId("skill-mode-source").click();

    await expect(page.getByTestId("source-import-empty")).toContainText("Settings → Sources");
  });
});
