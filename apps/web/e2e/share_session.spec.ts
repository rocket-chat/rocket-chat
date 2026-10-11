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

  test("explains when the organization has turned public links off", async ({ page, request }) => {
    await allowPublicLinks(request, false);
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "No Links" } });
    const sessionId: string = (await created.json()).session_id;

    await page.goto(`/chat/${sessionId}`);
    await page.getByTestId("share-trigger").click();

    await expect(page.getByTestId("share-dialog")).toContainText("Public links are turned off");
    await expect(page.getByTestId("share-create-link")).toHaveCount(0);
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
