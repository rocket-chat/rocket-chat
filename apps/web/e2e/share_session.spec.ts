import AxeBuilder from "@axe-core/playwright";
import { test, expect, type APIRequestContext } from "@playwright/test";

const API = "http://localhost:8000";

async function allowPublicLinks(request: APIRequestContext, enabled: boolean): Promise<void> {
  const saved = await request.put(`${API}/v1/admin/settings/general`, {
    data: { config: { public_session_links: enabled } },
  });
  expect(saved.ok()).toBeTruthy();
}

test.describe("Sharing a session", () => {
  test.afterEach(async ({ request }) => {
    await allowPublicLinks(request, false);
  });

  test("creates a public link that anyone can open, then revokes it", async ({ page, browser, request }) => {
    await allowPublicLinks(request, true);
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Share Me" } });
    const sessionId: string = (await created.json()).session_id;

    await page.goto(`/chat/${sessionId}`);
    await page.getByTestId("share-trigger").click();
    const dialog = page.getByTestId("share-dialog");
    await expect(dialog).toBeVisible();

    await page.getByTestId("share-create-link").click();
    const link = page.getByTestId("share-link");
    await expect(link).toHaveValue(/\/shared\/.+/);
    const url = await link.inputValue();

    // A visitor with no session at all can open it.
    const visitor = await browser.newContext({ baseURL: undefined });
    const shared = await visitor.newPage();
    await shared.goto(url);
    await expect(shared.getByTestId("shared-title")).toHaveText("Share Me");
    await visitor.close();

    await page.getByTestId("share-revoke-link").click();
    await expect(page.getByTestId("share-create-link")).toBeVisible();

    const afterRevoke = await browser.newContext();
    const gone = await afterRevoke.newPage();
    await gone.goto(url);
    await expect(gone.getByTestId("shared-unavailable")).toBeVisible();
    await afterRevoke.close();
  });

  test("the owner shares a session with a team, and the sharing view lists it", async ({ page, request }) => {
    const team = await request.post(`${API}/v1/teams`, { data: { name: `Share team ${Date.now().toString(36)}` } });
    expect(team.status()).toBe(201);
    const { id: teamId, name: teamName } = (await team.json()) as { id: string; name: string };
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Share With Team" } });
    const sessionId: string = (await created.json()).session_id;

    await page.goto(`/chat/${sessionId}`);
    await page.getByTestId("share-trigger").click();
    const dialog = page.getByTestId("share-dialog");
    await dialog.getByTestId("share-teams").selectOption([teamId]);
    const saved = page.waitForResponse((res) => res.url().endsWith(`/v1/sessions/${sessionId}/sharing`) && res.request().method() === "PUT");
    await dialog.getByTestId("share-save").click();
    const body = (await (await saved).json()) as { teams: { id: string; name: string }[] };
    expect(body.teams).toEqual([{ id: teamId, name: teamName }]);
    await expect(dialog.getByTestId("share-teams-current")).toContainText(teamName);
    const results = await new AxeBuilder({ page }).include("[data-testid=share-dialog]").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);

    const view = (await (await request.get(`${API}/v1/sessions/${sessionId}/sharing`)).json()) as { teams: { id: string }[] };
    expect(view.teams.map((t) => t.id)).toEqual([teamId]);
  });

  test("opens over the whole viewport, not inside the header", async ({ page, request }) => {
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Share Fit" } });
    const sessionId: string = (await created.json()).session_id;
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(`/chat/${sessionId}`);
    await page.getByTestId("share-trigger").click();

    const box = await page.getByTestId("share-dialog").boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(720);
  });

  test("explains when the organization has turned public links off", async ({ page, request }) => {
    await allowPublicLinks(request, false);
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "No Links" } });
    const sessionId: string = (await created.json()).session_id;

    await page.goto(`/chat/${sessionId}`);
    await page.getByTestId("share-trigger").click();

    await expect(page.getByTestId("share-dialog")).toContainText("Public links are turned off");
    await expect(page.getByTestId("share-create-link")).toHaveCount(0);
  });

  test("the form waits for the current sharing state instead of being overwritten by it", async ({ page, request }) => {
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Slow Load" } });
    const sessionId: string = (await created.json()).session_id;

    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**/v1/sessions/${sessionId}/sharing`, async (route) => {
      if (route.request().method() === "GET") await gate;
      await route.continue();
    });

    await page.goto(`/chat/${sessionId}`);
    await page.getByTestId("share-trigger").click();
    await expect(page.getByTestId("share-org-wide")).toBeDisabled();
    await expect(page.getByTestId("share-emails")).toBeDisabled();

    release();
    await expect(page.getByTestId("share-org-wide")).toBeEnabled();
    await page.getByTestId("share-org-wide").check();
    await page.getByTestId("share-save").click();
    await expect(page.getByTestId("share-dialog")).toContainText("Saved.");
    expect((await (await request.get(`${API}/v1/sessions/${sessionId}/sharing`)).json()).org_wide).toBe(true);
  });

  test("shares with the whole organization", async ({ page, request }) => {
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Org Wide" } });
    const sessionId: string = (await created.json()).session_id;

    await page.goto(`/chat/${sessionId}`);
    await page.getByTestId("share-trigger").click();
    await page.getByTestId("share-org-wide").check();
    await page.getByTestId("share-save").click();
    await expect(page.getByTestId("share-dialog")).toContainText("Saved.");

    const state = await (await request.get(`${API}/v1/sessions/${sessionId}/sharing`)).json();
    expect(state.org_wide).toBe(true);
  });
});
