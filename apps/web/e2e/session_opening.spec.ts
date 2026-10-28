import { test, expect, type Page, type WebSocketRoute } from "@playwright/test";

const BACKEND = "http://localhost:8000";

async function seededSession(page: Page, title: string, prompt: string): Promise<{ id: string; record: object }> {
  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title } });
  const id = (await created.json()).session_id as string;
  await page.request.post(`${BACKEND}/v1/dev/sessions/${id}/transcript`, {
    data: { messages: [{ role: "user", content: prompt }] },
  });
  const record = await (await page.request.get(`${BACKEND}/v1/sessions/${id}`)).json();
  return { id, record };
}

/** A turn that has run one command and is thinking about the next, as a tab connecting now sees it. */
function syncWithRunningTurn(record: object): object {
  const startedAt = Math.floor(Date.now() / 1000) - 30;
  return {
    type: "sync",
    session: record,
    last_seq: 5,
    running_turn: {
      turn_index: 1,
      started_at: startedAt,
      model: "test/model",
      status: "running",
      steps: [
        {
          id: "call_1",
          name: "bash_exec",
          arguments: { command: "pytest tests/cache" },
          result: "3 passed",
          status: "success",
          seq: 4,
          reasoning: "Run the cache tests first.",
          created_at: startedAt + 5,
        },
      ],
      current_reasoning: "Then read the eviction path.",
      // The thought above is what was published through this sequence.
      seq: 5,
    },
    // The same turn as events from its start: what the steps already show must not render again.
    catch_up: {
      from_seq: 0,
      events: [
        { event_type: "status_changed", payload: { status: "running" }, seq: 1 },
        { event_type: "reasoning_token", payload: { delta: "Run the cache tests first." }, seq: 2 },
        {
          event_type: "tool_started",
          payload: { id: "call_1", name: "bash_exec", arguments: { command: "pytest tests/cache" } },
          seq: 3,
        },
        {
          event_type: "tool_completed",
          payload: {
            id: "call_1",
            name: "bash_exec",
            arguments: { command: "pytest tests/cache" },
            status: "success",
            result: "3 passed",
          },
          seq: 4,
        },
        { event_type: "reasoning_token", payload: { delta: "Then read the eviction path." }, seq: 5 },
      ],
    },
  };
}

test("a conversation first in the list waits for its transcript instead of showing as empty", async ({ page }) => {
  const { id, record } = await seededSession(page, "Opening", "Look into the cache");
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    socket = ws;
  });
  // Only the sidebar listing answers: its row names the session but has no transcript.
  await page.route(`**/v1/sessions/${id}?tail=50`, () => {});

  await page.goto(`/chat/${id}`);
  await expect(page.locator(`[data-testid="session-row"][data-session-id="${id}"]`)).toBeVisible();
  await expect(page.getByTestId("session-loading-screen")).toBeVisible();
  await expect(page.getByTestId("cockpit-empty-state")).toHaveCount(0);

  await expect.poll(() => socket !== null).toBe(true);
  socket!.send(JSON.stringify({ type: "sync", session: record, last_seq: 0 }));
  await expect(page.getByTestId("flight-log-stream")).toContainText("Look into the cache");
  await expect(page.getByTestId("session-loading-screen")).toHaveCount(0);
});

test("a turn running when the tab connects shows each of its steps once", async ({ page }) => {
  const { id, record } = await seededSession(page, "Running", "Fix the cache");
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    ws.send(JSON.stringify(syncWithRunningTurn(record)));
  });

  await page.goto(`/chat/${id}`);
  const stream = page.getByTestId("flight-log-stream");
  await expect(stream).toContainText("Fix the cache");
  const steps = page.getByTestId("activity-step");
  // The thought in progress keeps its group open without a click.
  await expect(steps.filter({ hasText: "Then read the eviction path." })).toHaveCount(1);
  await expect(steps.filter({ hasText: "Run the cache tests first." })).toHaveCount(1);
  await expect(steps.filter({ hasText: "pytest tests/cache" })).toHaveCount(1);
});

test("switching conversations opens the next one from its socket's snapshot", async ({ page }) => {
  const first = await seededSession(page, "First", "First conversation prompt");
  const second = await seededSession(page, "Second", "Second conversation prompt");
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    if (ws.url().includes(second.id)) {
      ws.send(JSON.stringify(syncWithRunningTurn(second.record)));
    } else {
      ws.connectToServer();
    }
  });
  // The snapshot alone must open it: the direct read never answers.
  await page.route(`**/v1/sessions/${second.id}?tail=50`, (route) => route.abort());

  await page.goto(`/chat/${first.id}`);
  await expect(page.getByTestId("flight-log-stream")).toContainText("First conversation prompt");

  await page.locator(`[data-testid="session-row"][data-session-id="${second.id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/chat/${second.id}$`));
  const stream = page.getByTestId("flight-log-stream");
  await expect(stream).toContainText("Second conversation prompt");
  await expect(stream).not.toContainText("First conversation prompt");
  await expect(page.getByTestId("activity-step").filter({ hasText: "pytest tests/cache" })).toHaveCount(1);
});

test("a running group stays open between one thought and the next", async ({ page }) => {
  const { id, record } = await seededSession(page, "Following", "Keep going");
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    socket = ws;
    ws.send(JSON.stringify(syncWithRunningTurn(record)));
  });

  await page.goto(`/chat/${id}`);
  const toggle = page.getByTestId("action-group-toggle");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");

  // A tool call ends the thought; the group it belongs to does not fold up and reopen.
  socket!.send(
    JSON.stringify({
      event_type: "tool_started",
      session_id: id,
      seq: 6,
      payload: { id: "call_2", name: "file_read", arguments: { path: "cache/evict.py" } },
    }),
  );
  await expect(page.getByTestId("activity-step").filter({ hasText: "cache/evict.py" })).toHaveCount(1);
  await expect(toggle).toHaveAttribute("aria-expanded", "true");

  socket!.send(
    JSON.stringify({ event_type: "status_changed", session_id: id, seq: 7, payload: { status: "completed", final: true } }),
  );
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

async function longSession(page: Page, title: string, marker: string): Promise<string> {
  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title } });
  const id = (await created.json()).session_id as string;
  const messages = Array.from({ length: 130 }, (_, i) => [
    { role: "user", content: `${marker} question ${i}` },
    { role: "assistant", content: `${marker} answer ${i}\n\n\`\`\`\nline one\nline two\nline three\n\`\`\`` },
  ]).flat();
  await page.request.post(`${BACKEND}/v1/dev/sessions/${id}/transcript`, { data: { messages } });
  return id;
}

const distanceFromEnd = (page: Page) =>
  page
    .getByTestId("flight-log-stream")
    .evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);

test("a conversation opens at its end, even after scrolling up in the previous one", async ({ page }) => {
  const first = await longSession(page, "Long first", "Alpha");
  const second = await longSession(page, "Long second", "Bravo");

  await page.goto(`/chat/${first}`);
  await expect(page.getByTestId("flight-log-stream")).toContainText("Alpha answer 129");
  await expect.poll(() => distanceFromEnd(page)).toBeLessThan(80);

  await page.getByTestId("flight-log-stream").evaluate((el) => el.scrollTo({ top: 0 }));
  await expect(page.getByTestId("scroll-to-latest")).toBeVisible();

  await page.locator(`[data-testid="session-row"][data-session-id="${second}"]`).click();
  await expect(page.getByTestId("flight-log-stream")).toContainText("Bravo answer 129");
  await expect.poll(() => distanceFromEnd(page)).toBeLessThan(80);
});

test("a turn running on another replica shows its thought once, then carries on live", async ({ page }) => {
  const { id, record } = await seededSession(page, "Elsewhere", "Find the leak");
  const startedAt = Math.floor(Date.now() / 1000) - 30;
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    socket = ws;
    // What a replica not running the turn sends: the stored steps, the live part another replica
    // reported through sequence 7, and no catch-up of its own.
    ws.send(
      JSON.stringify({
        type: "sync",
        session: record,
        last_seq: 5,
        catch_up: null,
        running_turn: {
          turn_index: 1,
          started_at: startedAt,
          status: "running",
          seq: 7,
          steps: [
            {
              id: "call_1",
              name: "bash_exec",
              arguments: { command: "ps aux" },
              result: "ok",
              status: "success",
              seq: 4,
              created_at: startedAt + 5,
            },
          ],
          current_reasoning: "The cache never evicts.",
          current_text: "",
        },
      }),
    );
  });

  await page.goto(`/chat/${id}`);
  const steps = page.getByTestId("activity-step");
  await expect(steps.filter({ hasText: "The cache never evicts." })).toHaveCount(1);
  await expect.poll(() => socket !== null).toBe(true);

  // Published on the other replica before its snapshot: arriving now, it is already shown.
  socket!.send(JSON.stringify({ event_type: "reasoning_token", session_id: id, seq: 7, payload: { delta: " The cache never evicts." } }));
  socket!.send(JSON.stringify({ event_type: "reasoning_token", session_id: id, seq: 8, payload: { delta: " Its keys never expire." } }));

  const thought = steps.filter({ hasText: "The cache never evicts." });
  await expect(page.getByTestId("flight-log-stream")).toContainText("Its keys never expire.");
  await expect(thought).toHaveCount(1);
  await expect(page.getByTestId("flight-log-stream")).not.toContainText("evicts. The cache never evicts.");
});
