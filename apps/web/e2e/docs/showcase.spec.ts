import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";

/**
 * The README's and the launch posts' images, in doc/assets/showcase, from a deterministic stack
 * (stub model, fake sandbox, seeded conversations). Skipped unless DOCS_SCREENSHOTS=1:
 *
 *     pnpm run docs:screenshots
 */
test.skip(!process.env.DOCS_SCREENSHOTS, "Only runs when regenerating the documentation images");

const BACKEND = "http://localhost:8000";
const OUT = path.resolve(__dirname, "../../../../doc/assets/showcase");
const PERMALINK = "https://acme.slack.com/archives/C123/p1700000000000100";

test.use({ viewport: { width: 1280, height: 800 }, colorScheme: "light" });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem("rocket.composerHintsSeen", "1");
    } catch {
      // Storage blocked: nothing to mark.
    }
  });
});

const seeded: string[] = [];
test.afterEach(async ({ request }) => {
  for (const id of seeded.splice(0)) await request.delete(`${BACKEND}/v1/sessions/${id}`);
});

const FIX = {
  role: "assistant",
  content:
    "Fixed. `refund_amount` rounded each line item separately, so a refund of three items at €3.335 lost a cent.\n\nI now round the total once, in `billing/refunds.py`, and added a regression test. All 48 tests pass.",
  reasoning: "Rounding per line item accumulates error, so the fix is to round once at the end.",
  tool_calls: [
    { id: "c1", name: "clone_repository", arguments: { repository: "acme/billing-api" }, result: "Cloned acme/billing-api into /workspace/billing-api", status: "success" },
    {
      id: "c2",
      name: "file_edit",
      arguments: {
        path: "billing-api/billing/refunds.py",
        target_block: "return sum(round(item.amount, 2) for item in items)",
        replacement_block: "return round(sum(item.amount for item in items), 2)",
      },
      result: "ok",
      status: "success",
    },
    { id: "c3", name: "bash_exec", arguments: { command: "cd billing-api && uv run pytest -q" }, result: "48 passed", status: "success" },
    { id: "c4", name: "git_open_pr", arguments: { title: "fix: round refunds once, on the total" }, result: "Opened acme/billing-api#412", status: "success" },
  ],
};

async function seed(request: APIRequestContext, title: string, messages: unknown[]): Promise<string> {
  const created = await request.post(`${BACKEND}/v1/sessions`, { data: { title } });
  const { session_id: sessionId } = await created.json();
  seeded.push(sessionId);
  await request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, { data: { messages } });
  return sessionId as string;
}

/** The session as the Slack bridge would have written it: its thread, and several people in it. */
async function asSlackThread(page: Page, sessionId: string): Promise<void> {
  const slack = { slack_channel_id: "C123", slack_thread_ts: "1700000000.000100", slack_permalink: PERMALINK };
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((message) => server.send(message));
    server.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.session?.session_id === sessionId) {
        frame.session.source = "slack";
        frame.session.metadata = { ...frame.session.metadata, ...slack };
      }
      ws.send(JSON.stringify(frame));
    });
  });
  await page.route(/\/v1\/sessions(\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const rows = (await response.json()) as Array<Record<string, unknown>>;
    const patched = rows.map((row) =>
      row.session_id === sessionId ? { ...row, source: "slack", metadata: { ...(row.metadata as object), ...slack } } : row,
    );
    await route.fulfill({ response, json: patched });
  });
}

const DECISION = {
  event_type: "decision_required",
  payload: {
    question_id: "q_showcase",
    question_text: "The build is blocked: master pins protobuf-go to a commit that no longer exists. How should I proceed?",
    options: [
      "Re-pin protobuf-go to master on this branch, then build and test",
      "Push the branches and let CI compile them",
      "Stop here: I'll ask the owners to fix the pin on master",
    ],
    is_multi_select: false,
    default_recommended_option: "Re-pin protobuf-go to master on this branch, then build and test",
  },
};

async function showQuestion(page: Page): Promise<void> {
  let socket: { send: (m: string) => void } | null = null;
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    const server = ws.connectToServer();
    socket = ws;
    ws.onMessage((message) => server.send(message));
    server.onMessage((message) => ws.send(message));
  });
  const card = page.getByTestId("decision-card");
  await page.goto("/chat/draft");
  return void (await expect
    .poll(
      async () => {
        socket?.send(JSON.stringify(DECISION));
        return card.count();
      },
      { timeout: 15000, intervals: [300, 500, 1000] },
    )
    .toBeGreaterThan(0));
}

test.describe("dark", () => {
  test.use({ colorScheme: "dark" });

  test("a finished task, in the dark theme", async ({ page, request }) => {
    const sessionId = await seed(request, "Fix the refund rounding bug", [
      { role: "user", content: "Clone acme/billing-api, find why test_refund_rounding fails and fix it. Open a pull request." },
      FIX,
    ]);
    await page.goto(`/chat/${sessionId}`);
    await expect(page.getByText("Fixed. ")).toBeVisible();
    await page.screenshot({ path: path.join(OUT, "task-dark.png") });
  });
});

test("a question card instead of a guess", async ({ page, request }) => {
  const sessionId = await seed(request, "Fill customer identity fields", [
    { role: "user", content: "Proceed with SVC-22487 and SVC-22488: add user.identity_number and send it to Stripe and Monext." },
  ]);
  await page.route("**/chat/draft", (route) => route.fulfill({ status: 302, headers: { location: `/chat/${sessionId}` } }));
  await showQuestion(page);
  await page.screenshot({ path: path.join(OUT, "question.png") });
});

test("a Slack thread, with the people in it", async ({ page, request }) => {
  const sessionId = await seed(request, "Checkout 500s since this morning", [
    { role: "user", content: "[Speaker: Ada Lovelace (<@U1>)] Checkout returns 500 since this morning, can you look?", author_user_id: "slack:T1:U1" },
    {
      role: "assistant",
      content:
        "Found it: the 09:12 deploy renamed `PAYMENT_TIMEOUT` to `PAYMENT_TIMEOUT_MS`, and checkout still reads the old one, so every call times out at 0 ms.\n\nI opened acme/checkout#88 that reads the new name with a fallback. CI is green.",
    },
    { role: "user", content: "[Speaker: Grace Hopper (<@U2>)] Nice. Can you add a test so this can't happen again?", author_user_id: "slack:T1:U2" },
    { role: "assistant", content: "Added `test_payment_timeout_reads_the_new_variable` to the same PR. 212 tests pass." },
  ]);
  await asSlackThread(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByTestId("thread-banner")).toBeVisible();
  await expect(page.getByTestId("user-message-author")).toHaveCount(2);
  await page.screenshot({ path: path.join(OUT, "slack-thread.png") });
});

test("an agent, shaped to the work", async ({ page }) => {
  await page.goto("/settings/agents");
  await page.getByTestId("agent-new").click();
  await page.getByTestId("agent-name").fill("Backend reviewer");
  await page.getByTestId("agent-section-model").click();
  await expect(page.getByTestId("agent-editor")).toBeVisible();
  await page.screenshot({ path: path.join(OUT, "agent-editor.png") });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 402, height: 874 }, isMobile: true, hasTouch: true });

  test("answering from a phone", async ({ page, request }) => {
    const sessionId = await seed(request, "Fill customer identity fields", [
      { role: "user", content: "Proceed with SVC-22487 and SVC-22488." },
    ]);
    await page.route("**/chat/draft", (route) => route.fulfill({ status: 302, headers: { location: `/chat/${sessionId}` } }));
    await showQuestion(page);
    await page.screenshot({ path: path.join(OUT, "question-phone.png") });
  });
});

test("the architecture diagram, as a social card", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 630 });
  await page.goto(`file://${path.join(OUT, "architecture.svg")}`);
  await page.screenshot({ path: path.join(OUT, "architecture.png") });
});
