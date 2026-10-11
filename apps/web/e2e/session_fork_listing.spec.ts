import { test, expect, type APIRequestContext } from "@playwright/test";

/**
 * Session listing ergonomics and forking.
 *
 * The backend runs with the in-memory store and fake sandbox for E2E, so these tests
 * exercise the real cockpit against the real ASGI surface without a database or Docker.
 * Sessions are created over REST so a test never depends on turn latency.
 */

const BACKEND = "http://localhost:8000";

async function createSession(
  request: APIRequestContext,
  data: Record<string, unknown>
): Promise<string> {
  const res = await request.post(`${BACKEND}/v1/sessions`, { data });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).session_id as string;
}

test.describe("Session fork, pin and archive", () => {
  test.describe.configure({ mode: "serial" });

  test("a pinned session survives a reload", async ({ page, request }) => {
    const sessionId = await createSession(request, { title: `Pin Me ${Date.now()}` });
    await page.goto(`/chat/${sessionId}`);

    const row = page.locator(`[data-testid="session-row"][data-session-id="${sessionId}"]`);
    await expect(row).toBeVisible();
    await row.hover();
    await row.getByTestId("pin-session-btn").click();
    await expect(row).toHaveAttribute("data-pinned", "true");

    await page.reload();
    const reloaded = page.locator(`[data-testid="session-row"][data-session-id="${sessionId}"]`);
    await expect(reloaded).toHaveAttribute("data-pinned", "true");
    await expect(reloaded.getByTestId("session-pinned-indicator")).toBeVisible();
  });

  test("an archived session is hidden by default", async ({ page, request }) => {
    const title = `Archive Me ${Date.now()}`;
    const sessionId = await createSession(request, { title });
    await page.goto(`/chat/${sessionId}`);

    const row = page.locator(`[data-testid="session-row"][data-session-id="${sessionId}"]`);
    await expect(row).toBeVisible();
    await row.hover();
    const archived = page.waitForResponse(
      (response) => response.url().includes(`/v1/sessions/${sessionId}`) && response.request().method() === "PATCH",
    );
    await row.getByTestId("archive-session-btn").click();
    expect((await archived).ok()).toBeTruthy();

    await page.reload();
    const hidden = page.locator(`[data-testid="session-row"][data-session-id="${sessionId}"]`);
    await expect(hidden).toHaveCount(0);

    await page.getByTestId("toggle-archived-btn").click();
    await expect(page.getByTestId("toggle-archived-btn")).toHaveAttribute("aria-pressed", "true");

    // Other specs keep creating sessions, so the row can sit beyond the first page; ask the
    // API for it by title instead of paging the sidebar.
    const listing = await request.get(`${BACKEND}/v1/sessions`, { params: { archived: "true", q: title } });
    expect(listing.ok()).toBeTruthy();
    const found = (await listing.json()) as Array<{ session_id: string; is_archived: boolean }>;
    expect(found.find((item) => item.session_id === sessionId)?.is_archived).toBe(true);
  });

  test("a fork appears with its own URL and fork relationship", async ({ page, request }) => {
    const parentId = await createSession(request, { title: `Fork Parent ${Date.now()}` });
    const patch = await request.post(`${BACKEND}/v1/dev/sessions/${parentId}/transcript`, {
      data: {
        messages: [
          { role: "user", content: "Branch this conversation" },
          { role: "assistant", content: "This is the answer to branch from." },
        ],
      },
    });
    expect(patch.ok()).toBeTruthy();

    await page.goto(`/chat/${parentId}`);
    await expect(page).toHaveURL(`/chat/${parentId}`);

    const forkButton = page.getByTestId("fork-from-here").first();
    await expect(forkButton).toBeVisible();
    await forkButton.click();

    // The parent URL is itself `/chat/session_...`, so waiting on the generic pattern
    // would match before navigation: wait until the path actually leaves the parent.
    await expect
      .poll(() => new URL(page.url()).pathname, { timeout: 15000 })
      .not.toEqual(`/chat/${parentId}`);
    const forkPath = new URL(page.url()).pathname;
    const forkId = forkPath.split("/").pop() ?? "";

    const forkRow = page.locator(`[data-testid="session-row"][data-session-id="${forkId}"]`);
    await expect(forkRow).toBeVisible();
    await expect(forkRow.getByTestId("session-fork-indicator")).toBeVisible();
    const parentRow = page.locator(`[data-testid="session-row"][data-session-id="${parentId}"]`);
    await expect(parentRow).toBeVisible();
  });
});
