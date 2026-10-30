import { test, expect, type Page } from "@playwright/test";

const API = "http://localhost:8000";
const PERMALINK = "https://acme.slack.com/archives/C123/p1700000000000100";
const SLACK = {
  slack_channel_id: "C123",
  slack_thread_ts: "1700000000.000100",
  slack_permalink: PERMALINK,
};

/** Give a session the Slack provenance only the Slack bridge writes, in the socket snapshot and
 * in the sidebar list; optionally a conversation between several people. */
async function asSlackSession(page: Page, sessionId: string, history?: unknown[]) {
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((message) => server.send(message));
    server.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.session?.session_id === sessionId) {
        frame.session.source = "slack";
        frame.session.metadata = { ...frame.session.metadata, ...SLACK };
        if (history) frame.session.conversation_history = history;
      }
      ws.send(JSON.stringify(frame));
    });
  });
  await page.route(/\/v1\/sessions(\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const rows = (await response.json()) as Array<Record<string, unknown>>;
    const patched = rows.map((row) =>
      row.session_id === sessionId ? { ...row, source: "slack", metadata: { ...(row.metadata as object), ...SLACK } } : row
    );
    await route.fulfill({ response, json: patched });
  });
}

async function newSession(request: import("@playwright/test").APIRequestContext, title: string): Promise<string> {
  const created = await request.post(`${API}/v1/sessions`, { data: { title } });
  return (await created.json()).session_id as string;
}

test("a Slack session shows its thread in a banner and opens it from the sidebar icon", async ({ page, request, context }) => {
  const sessionId = await newSession(request, `Thread ${Date.now()}`);
  await asSlackSession(page, sessionId);
  await page.goto(`/chat/${sessionId}`);

  const banner = page.getByTestId("thread-banner");
  await expect(banner).toContainText("bound to a Slack thread");
  await expect(banner.getByTestId("open-thread")).toHaveAttribute("href", PERMALINK);

  const row = page.locator(`[data-testid="session-row"][data-session-id="${sessionId}"]`);
  await expect(row.getByTestId("session-platform")).toHaveAttribute("data-platform", "slack");
  const opened = context.waitForEvent("page");
  await row.getByTestId("open-thread-from-row").click();
  const thread = await opened;
  expect(thread.url()).toContain("acme.slack.com");
  await thread.close();
  // The click opened the thread, not the session in this tab.
  await expect(page).toHaveURL(new RegExp(`/chat/${sessionId}`));
});

test("a web session has no thread banner", async ({ page, request }) => {
  const sessionId = await newSession(request, "Plain web session");
  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByRole("heading", { name: "Plain web session" })).toBeVisible();
  await expect(page.getByTestId("thread-banner")).toHaveCount(0);
});

test("a session is renamed from its title, and the name stays", async ({ page, request }) => {
  const sessionId = await newSession(request, "Old name");
  await page.goto(`/chat/${sessionId}`);
  await page.getByTestId("session-title").click();
  const input = page.getByTestId("session-title-input");
  await input.fill("Fix the login redirect");
  await input.press("Enter");
  await expect(page.getByTestId("session-title")).toHaveText("Fix the login redirect");
  const stored = await (await request.get(`${API}/v1/sessions/${sessionId}`)).json();
  expect(stored.title).toBe("Fix the login redirect");
  expect(stored.metadata.title_source).toBe("user");
});

test("a session is renamed from the sidebar menu", async ({ page, request }) => {
  const sessionId = await newSession(request, `Menu rename ${Date.now()}`);
  await page.goto(`/chat/${sessionId}`);
  const row = page.locator(`[data-testid="session-row"][data-session-id="${sessionId}"]`);
  await row.click({ button: "right" });
  await page.getByTestId("rename-session").click();
  const input = row.getByTestId("session-rename-input");
  await input.fill("Renamed from the list");
  await input.press("Enter");
  await expect(row).toContainText("Renamed from the list");
  const stored = await (await request.get(`${API}/v1/sessions/${sessionId}`)).json();
  expect(stored.title).toBe("Renamed from the list");
});

test("in a thread with several people, each message is signed by its author", async ({ page, request }) => {
  const sessionId = await newSession(request, `Team thread ${Date.now()}`);
  const history = [
    { role: "user", content: "[Speaker: Ada Lovelace (<@U1>)] can you check the build?", author_user_id: "slack:T1:U1" },
    { role: "assistant", content: "On it." },
    { role: "user", content: "[Speaker: grace (<@U2>)] and the tests too", author_user_id: "member_grace" },
    { role: "assistant", content: "Done." },
  ];
  await asSlackSession(page, sessionId, history);
  await page.route(`**/v1/sessions/${sessionId}/participants`, (route) =>
    route.fulfill({
      json: {
        participants: [
          { user_id: "slack:T1:U1", name: "Slack user U1", guest_of: "slack" },
          { user_id: "member_grace", name: "Grace Hopper", guest_of: null },
        ],
      },
    })
  );
  await page.goto(`/chat/${sessionId}`);

  const authors = page.getByTestId("user-message-author");
  await expect(authors).toHaveCount(2);
  // A guest is named by Slack, a linked member by their account.
  await expect(authors.nth(0)).toHaveText("Ada Lovelace");
  await expect(authors.nth(1)).toHaveText("Grace Hopper");
  await expect(page.getByTestId("user-message").first()).not.toContainText("[Speaker:");
});
