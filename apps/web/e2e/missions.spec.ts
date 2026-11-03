import { createHmac } from "node:crypto";
import AxeBuilder from "@axe-core/playwright";
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://localhost:8000";

async function newTeam(request: APIRequestContext, prefix: string): Promise<{ id: string; name: string }> {
  const res = await request.post(`${API}/v1/teams`, { data: { name: `${prefix} ${Date.now().toString(36)}` } });
  expect(res.status()).toBe(201);
  return (await res.json()) as { id: string; name: string };
}

async function expectNoBlockingViolations(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
}

test.describe("Missions", () => {
  test("a pull request review mission is created from its preset for a team", async ({ page, request }) => {
    const team = await newTeam(request, "Review team");
    await page.goto("/");
    await page.getByTestId("sidebar-missions-link").click();
    await expect(page).toHaveURL(/\/missions/);

    await page.getByTestId("mission-new").click();
    await page.getByTestId("mission-preset").selectOption("pr-review");
    await expect(page.getByTestId("mission-trigger-source-0")).toHaveValue("github_pr");
    await expect(page.getByTestId("mission-mode")).toHaveValue("review");
    await expect(page.getByTestId("mission-delivery-github_review")).toBeVisible();
    await page.getByTestId("mission-owner").selectOption(`team:${team.id}`);
    const name = `PR review ${Date.now().toString(36)}`;
    await page.getByTestId("mission-name").fill(name);

    const created = page.waitForResponse((res) => res.url().endsWith("/v1/missions") && res.request().method() === "POST");
    await page.getByTestId("mission-save").click();
    const body = (await (await created).json()) as { scope: string; team_id: string; mode: string; deliveries: { kind: string }[] };
    expect(body).toMatchObject({ scope: "team", team_id: team.id, mode: "review" });
    expect(body.deliveries.map((d) => d.kind)).toContain("github_review");

    const card = page.getByTestId(`mission-group-team:${team.id}`).getByTestId("mission-item").filter({ hasText: name });
    await expect(card.getByTestId("owner-badge")).toHaveText(team.name);
    await expect(card).toContainText("A pull request review");
  });

  test("Run now starts a run, listed with its session; a mission is paused and deleted", async ({ page, request }) => {
    const name = `Nightly triage ${Date.now().toString(36)}`;
    const res = await request.post(`${API}/v1/missions`, {
      data: { name, instructions: "Triage new issues", triggers: [{ source: "schedule", schedule: "0 3 * * *" }] },
    });
    expect(res.status()).toBe(200);
    await page.goto("/missions");
    const card = page.getByTestId("mission-item").filter({ hasText: name });
    await expect(card).toContainText("Every night at 03:00");

    await card.getByTestId("mission-run").click();
    const run = card.getByTestId("mission-runs").getByTestId("mission-run").first();
    await expect(run).toBeVisible();
    await expect(run.getByRole("link", { name: "Open the session" })).toHaveAttribute("href", /\/chat\/.+/);

    await card.getByTestId("mission-toggle").uncheck();
    await expect(card).toContainText("paused");
    await card.getByRole("button", { name: `Delete ${name}` }).click();
    await page.getByTestId("confirm-accept").click();
    await expect(card).toHaveCount(0);
  });

  test("a webhook mission shows its secret once, and a signed call starts a run", async ({ page, request }) => {
    await page.goto("/missions");
    await page.getByTestId("mission-new").click();
    const name = `Alert triage ${Date.now().toString(36)}`;
    await page.getByTestId("mission-name").fill(name);
    await page.getByTestId("mission-trigger-source-0").selectOption("webhook");
    await page.getByTestId("mission-delivery-add").selectOption("webhook");
    await page.getByTestId("mission-webhook-url").fill("https://hooks.example.com/rocket");
    await page.getByTestId("mission-webhook-schema").fill("{ not json");
    await page.getByTestId("mission-save").click();
    await expect(page.getByTestId("mission-error")).toHaveText("The schema is not valid JSON.");
    // Saving checks a webhook delivery's URL resolves to a public address: no network here, so
    // the run reports to its session only.
    await page.getByRole("button", { name: "Remove A webhook (JSON)" }).click();

    const created = page.waitForResponse((res) => res.url().endsWith("/v1/missions") && res.request().method() === "POST");
    await page.getByTestId("mission-save").click();
    const saved = (await (await created).json()) as { id: string; inbound_path: string; inbound_secret: string };

    const dialog = page.getByTestId("mission-secret-dialog");
    await expect(dialog.getByTestId("mission-inbound-secret")).toHaveText(saved.inbound_secret);
    await expect(dialog.getByTestId("mission-inbound-curl")).toContainText("X-Rocket-Signature: sha256=$SIG");
    await dialog.getByTestId("mission-secret-close").click();
    await expect(dialog).toHaveCount(0);

    // Shown once: the list never returns it.
    await page.reload();
    const card = page.getByTestId("mission-item").filter({ hasText: name });
    await expect(card.getByTestId("mission-inbound-path")).toContainText(saved.inbound_path);
    await expect(page.getByText(saved.inbound_secret)).toHaveCount(0);

    // A call signed with the secret starts a run.
    const body = JSON.stringify({ alert: "disk full" });
    const timestamp = Math.floor(Date.now() / 1000);
    const signature = `sha256=${createHmac("sha256", saved.inbound_secret).update(`${timestamp}.${body}`).digest("hex")}`;
    const call = await request.post(`${API}${saved.inbound_path}`, {
      headers: { "Content-Type": "application/json", "X-Rocket-Timestamp": String(timestamp), "X-Rocket-Signature": signature },
      data: body,
    });
    expect(call.status()).toBe(200);
    expect(((await call.json()) as { session_id: string }).session_id).not.toBe("");
    const unsigned = await request.post(`${API}${saved.inbound_path}`, { headers: { "Content-Type": "application/json" }, data: body });
    expect(unsigned.status()).toBe(401);
  });

  test("a Slack delivery offers the organization's bots to every member", async ({ page }) => {
    // Without a Slack workspace in the test stack, the list of bots is fixed here.
    await page.route("**/v1/missions/chat-bots", (route) => route.fulfill({ json: [{ id: "bot_e2e", name: "Rocket E2E bot" }] }));
    await page.route("**/v1/me/access", (route) =>
      route.fulfill({ json: { org_id: "default_org", org_name: "Default organization", role: "member", roles: ["member"], capabilities: [] } }),
    );
    await page.goto("/missions");
    await page.getByTestId("mission-new").click();
    await page.getByTestId("mission-delivery-add").selectOption("chat");
    const delivery = page.getByTestId("mission-delivery-chat");
    await expect(delivery.getByLabel("Slack bot").locator("option")).toHaveText(["The organization's bot", "Rocket E2E bot"]);
  });

  test("a mission's form says what is missing before saving", async ({ page }) => {
    await page.goto("/missions");
    await page.getByTestId("mission-new").click();
    await page.getByTestId("mission-name").fill("Review without a pull request");
    await page.getByTestId("mission-mode").selectOption("review");
    await page.getByTestId("mission-save").click();
    await expect(page.getByTestId("mission-error")).toContainText("needs a pull request");
  });

  test("an unsafe schedule is explained, not silently accepted", async ({ request }) => {
    const res = await request.post(`${API}/v1/missions`, {
      data: { name: "x", triggers: [{ source: "schedule", schedule: "* * * * *" }] },
    });
    expect(res.status()).toBe(422);
    expect((await res.json()).detail).toContain("5 minutes");
  });

  for (const scheme of ["light", "dark"] as const) {
    test(`the missions page and its form have no serious or critical violations (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/missions");
      await page.getByTestId("mission-new").click();
      await page.getByTestId("mission-delivery-add").selectOption("chat");
      await expectNoBlockingViolations(page);
    });
  }
});

test.describe("Session templates", () => {
  test("save a template and start a configured session from it", async ({ page }) => {
    await page.goto("/missions");
    await page.getByTestId("template-new").click();
    await page.getByTestId("template-name").fill("E2E bug hunt");
    await page.getByTestId("template-prompt").fill("Hunt for the flaky test");
    await expect(page.getByTestId("template-owner")).toHaveValue("me");
    await page.getByTestId("template-create").click();

    const item = page.getByTestId("template-item").filter({ hasText: "E2E bug hunt" });
    await expect(item.getByTestId("owner-badge")).toHaveText("You");
    await item.getByTestId("template-start").click();

    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    await expect(page.getByText("Hunt for the flaky test").first()).toBeVisible({ timeout: 15000 });

    await page.goto("/missions");
    await page
      .getByTestId("template-item")
      .filter({ hasText: "E2E bug hunt" })
      .getByRole("button", { name: /Delete template/ })
      .click();
    await expect(page.getByTestId("template-item").filter({ hasText: "E2E bug hunt" })).toHaveCount(0);
  });

  test("a template is made for a team", async ({ page, request }) => {
    const team = await newTeam(request, "Template team");
    await page.goto("/missions");
    await page.getByTestId("template-new").click();
    const name = `Team playbook ${Date.now().toString(36)}`;
    await page.getByTestId("template-name").fill(name);
    await page.getByTestId("template-prompt").fill("Check the release notes");
    await page.getByTestId("template-owner").selectOption(`team:${team.id}`);
    const created = page.waitForResponse((res) => res.url().endsWith("/v1/templates") && res.request().method() === "POST");
    await page.getByTestId("template-create").click();
    expect(((await (await created).json()) as { scope: string; team_id: string })).toMatchObject({ scope: "team", team_id: team.id });
    await expect(page.getByTestId("template-item").filter({ hasText: name }).getByTestId("owner-badge")).toHaveText(team.name);
  });

  test("a personal template is edited and moved to a team after its preview", async ({ page, request }) => {
    const team = await newTeam(request, "Template move team");
    const name = `Movable playbook ${Date.now().toString(36)}`;
    await page.goto("/missions");
    await page.getByTestId("template-new").click();
    await page.getByTestId("template-name").fill(name);
    await page.getByTestId("template-prompt").fill("Summarise the open incidents");
    await page.getByTestId("template-create").click();
    const item = page.getByTestId("template-item").filter({ hasText: name });
    await expect(item.getByTestId("owner-badge")).toHaveText("You");

    await item.getByTestId("template-edit").click();
    await page.getByTestId("template-prompt").fill("Summarise the open incidents of the week");
    await page.getByTestId("template-owner").selectOption(`team:${team.id}`);
    await page.getByTestId("template-create").click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog.getByTestId("move-audience")).toContainText(`the members of the team ${team.name}`);
    const saved = page.waitForResponse((res) => /\/v1\/templates\/[^/]+$/.test(res.url()) && res.request().method() === "PUT");
    await dialog.getByTestId("confirm-accept").click();
    expect((await saved).status()).toBe(200);
    await expect(item.getByTestId("owner-badge")).toHaveText(team.name);
    await expect(item).toContainText("of the week");
  });
});
