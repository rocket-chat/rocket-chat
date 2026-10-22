import { test, expect, type WebSocketRoute } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Records the animated demo in the README (doc/assets/demo.gif): the real cockpit on the
 * deterministic stack (fake sandbox, no model call), with a person typing a request and the run
 * that answers it replayed live through the session's socket, as the backend would publish it.
 * The browser's own screencast is saved frame by frame with its timestamps; a second step,
 * scripts/make_demo_gif.py, captions it and assembles the GIF. Skipped unless DOCS_SCREENSHOTS=1:
 *
 *     pnpm run docs:demo
 */
test.skip(!process.env.DOCS_SCREENSHOTS, "Only runs when regenerating the documentation images");

const FRAMES = path.resolve(__dirname, "../../../../.demo-frames");
const PROMPT = "Fix the failing refund test in acme/billing-api, then run the tests.";
const EDIT = {
  path: "billing-api/billing/refunds.py",
  target_block: "return sum(round(item.amount, 2) for item in items)",
  replacement_block: "return round(sum(item.amount for item in items), 2)",
};
const ANSWER =
  "Fixed. `refund_amount` rounded each line item separately, so a refund of three items at €3.335 lost a cent.\n\nI now round the total once, in `billing/refunds.py`, and added a regression test. All 48 tests pass.";

test.use({ viewport: { width: 1280, height: 760 }, colorScheme: "light" });

// The cockpit shows its keyboard shortcuts once per browser; a fresh test browser is always a
// first visit, so mark them seen or the popover would sit over the whole recording.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem("rocket.composerHintsSeen", "1");
    } catch {
      // Storage blocked: nothing to mark.
    }
  });
});

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

test("the demo recording", async ({ page }) => {
  fs.rmSync(FRAMES, { recursive: true, force: true });
  fs.mkdirSync(FRAMES, { recursive: true });

  // The session's socket: the real backend answers as usual, except that the turn the page asks
  // for is not started, so the scripted run below is the only thing that happens.
  let route: WebSocketRoute | null = null;
  let synced = false;
  await page.route(/\/v1\/sessions\/[^/]+\/turns$/, (request) =>
    request.request().method() === "POST"
      ? request.fulfill({ status: 202, contentType: "application/json", body: "{}" })
      : request.fallback(),
  );
  await page.routeWebSocket(/\/v1\/sessions\/.*\/ws/, (ws) => {
    route = ws;
    const server = ws.connectToServer();
    server.onMessage((message) => {
      // Control frames (the first snapshot) come from the backend; its events do not.
      if (typeof message === "string" && message.includes('"event_type"')) return;
      ws.send(message);
      if (typeof message === "string" && message.includes('"sync"')) synced = true;
    });
    ws.onMessage((message) => {
      try {
        if (JSON.parse(message.toString()).type === "turn") return;
      } catch {
        // not JSON: forwarded all the same
      }
      server.send(message);
    });
  });
  let sessionId = "";
  const emit = (eventType: string, payload: Record<string, unknown>) =>
    route!.send(JSON.stringify({ event_type: eventType, session_id: sessionId, payload }));
  const stream = async (eventType: string, text: string, step: number, pause: number) => {
    for (let at = 0; at < text.length; at += step) {
      emit(eventType, { delta: text.slice(at, at + step) });
      await sleep(pause);
    }
  };

  await page.goto("/chat/draft");
  await expect(page.getByTestId("cockpit-empty-state")).toBeVisible();

  // The browser's own screencast, with the time of each frame.
  const marks: { t: number; caption: string }[] = [];
  const mark = (caption: string) => marks.push({ t: Date.now() / 1000, caption });
  const cdp = await page.context().newCDPSession(page);
  cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId: frame }) => {
    fs.writeFileSync(
      path.join(FRAMES, `${metadata.timestamp!.toFixed(3)}.png`),
      Buffer.from(data, "base64"),
    );
    await cdp.send("Page.screencastFrameAck", { sessionId: frame }).catch(() => undefined);
  });
  // PNG, not JPEG: a lossless frame is identical wherever the page did not change, so the GIF
  // needs no noise filtering (which leaves ghosts of anything that fades out).
  await cdp.send("Page.startScreencast", { format: "png", everyNthFrame: 1 });
  await sleep(900);

  // 1. Ask, in plain words.
  mark("1 · Ask for the outcome you want, in plain words");
  const input = page.getByTestId("instruction-input");
  await input.click();
  await input.pressSequentially(PROMPT, { delay: 32 });
  await sleep(500);
  fs.writeFileSync(path.join(FRAMES, "launch.json"), JSON.stringify({ t: Date.now() / 1000 }));
  mark("2 · It works in its own sandbox: reads, edits, runs your tests");
  await page.getByTestId("ignite-button").click();
  await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
  sessionId = page.url().split("/chat/")[1];
  // The backend's stub titler would call this conversation "stub title".
  await page.request.patch(`http://localhost:8000/v1/sessions/${sessionId}`, {
    data: { title: "Fix the failing refund test" },
  });
  await expect.poll(() => synced, { timeout: 15000 }).toBe(true);
  await sleep(800);

  // 2. It works: thinking, steps, a command's output, the edit, the answer, all as they come.
  emit("status_changed", { status: "running" });
  await stream(
    "reasoning_token",
    "The failing test compares a refund total to the cent. Rounding every line item separately accumulates error, so I should round the sum once. Let me look at the code first.",
    4,
    28,
  );
  const started = (id: string, name: string, args: Record<string, unknown>) =>
    emit("tool_started", { id, name, arguments: args });
  const completed = (
    id: string,
    name: string,
    args: Record<string, unknown>,
    result: string,
    ms: number,
  ) =>
    emit("tool_completed", { id, name, arguments: args, result, status: "completed", duration_ms: ms });

  const clone = { repository: "acme/billing-api" };
  started("c1", "clone_repository", clone);
  await sleep(1100);
  completed("c1", "clone_repository", clone, "Cloned acme/billing-api into /workspace/billing-api", 1100);

  const grep = { command: "grep -n round billing/refunds.py", cwd: "/workspace/billing-api" };
  started("c2", "bash_exec", grep);
  await sleep(500);
  completed("c2", "bash_exec", grep, "14:    return sum(round(item.amount, 2) for item in items)", 480);

  await stream("reasoning_token", " Found it, line 14. Rounding happens per item.", 4, 28);
  started("c3", "file_edit", EDIT);
  await sleep(700);
  completed("c3", "file_edit", EDIT, "ok", 640);

  const tests = { command: "uv run pytest -q", cwd: "/workspace/billing-api" };
  started("c4", "bash_exec", tests);
  const lines = [
    "tests/test_refunds.py ............                       [ 25%]",
    "tests/test_invoices.py ..................                [ 62%]",
    "tests/test_payments.py ..................                [100%]",
    "",
    "\u001b[32m48 passed\u001b[0m in 3.41s",
  ];
  for (const line of lines) {
    emit("log_chunk", { text: `${line}\r\n`, tool_call_id: "c4" });
    await sleep(520);
  }
  completed("c4", "bash_exec", tests, lines.join("\n"), 2900);
  await sleep(400);

  // 3. The answer is written out, then the work is there to open.
  mark("3 · You get the result, and every step is there to review");
  await stream("token", ANSWER, 5, 24);
  emit("status_changed", { status: "completed", final: true });
  await sleep(1500);

  await page.getByTestId("action-group-toggle").click();
  await sleep(900);
  await page.getByTestId("activity-step").filter({ hasText: /^Edited/ }).first().click();
  await expect(page.getByTestId("inline-diff").first()).toBeVisible();
  await sleep(2600);

  await cdp.send("Page.stopScreencast");
  fs.writeFileSync(path.join(FRAMES, "marks.json"), JSON.stringify(marks, null, 2));
});
