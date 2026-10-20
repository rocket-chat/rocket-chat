import { test, expect } from "@playwright/test";

const BACKEND = "http://localhost:8000";

async function seedSession(request: import("@playwright/test").APIRequestContext) {
  const created = await request.post(`${BACKEND}/v1/sessions`, { data: { title: "Layout seed" } });
  const { session_id: sessionId } = await created.json();
  const patch = await request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, {
    data: {
      messages: [
        { role: "user", content: "Fix the greeting and run the tests" },
        {
          role: "assistant",
          content: "The greeting is fixed; one unrelated test still fails.",
          reasoning: "The greeting lives in app.py, so I will edit it and then run the tests.",
          tool_calls: [
            {
              id: "c1",
              name: "file_edit",
              arguments: { path: "src/app.py", target_block: "print('helo')", replacement_block: "print('hello')" },
              result: "ok",
              status: "success",
            },
            {
              id: "c2",
              name: "bash_exec",
              arguments: { command: "cd /workspace/api && pytest -q", cwd: "/workspace" },
              reasoning: "Run the tests to check nothing else broke.",
              result: "1 failed, 12 passed",
              status: "error",
            },
          ],
        },
      ],
    },
  });
  expect(patch.ok()).toBeTruthy();
  return sessionId as string;
}

test.describe("Chat layout: minimal by default, detail on demand", () => {
  test("a new conversation offers starting points instead of an empty page", async ({ page }) => {
    await page.goto("/chat/draft");
    const empty = page.getByTestId("cockpit-empty-state");
    await expect(empty).toBeVisible();

    await empty.getByTestId("starter-prompt").first().click();
    await expect(page.getByTestId("instruction-input")).not.toHaveValue("");
  });

  test("work is summarised in one line and each step opens on demand", async ({ page, request }) => {
    const sessionId = await seedSession(request);
    await page.goto(`/chat/${sessionId}`);

    const toggle = page.getByTestId("action-group-toggle").first();
    await expect(toggle).toContainText("Edited 1 file");
    await expect(toggle).toContainText("Ran 1 command");
    await expect(toggle).toContainText("1 failed");
    // The answer is the content; none of the working is on screen yet.
    await expect(page.getByText("The greeting is fixed; one unrelated test still fails.")).toBeVisible();
    await expect(page.getByTestId("activity-step")).toHaveCount(0);

    await toggle.click();
    const steps = page.getByTestId("activity-step");
    // A step's saved reasoning is shown right before the step it led to, as it streamed;
    // the message's own reasoning (after the last step) comes last.
    await expect(steps).toHaveCount(4);
    await expect(steps.nth(1)).toContainText("Run the tests to check nothing else broke.");
    await expect(steps.nth(2)).toContainText("pytest -q");

    const edit = steps.filter({ hasText: "src/app.py" });
    await expect(edit).toContainText("+1");
    await expect(edit).toContainText("−1");
    await expect(page.getByTestId("inline-diff")).toHaveCount(0);
    await edit.click();
    const diff = page.getByTestId("inline-diff");
    await expect(diff).toContainText("-print('helo')");
    await expect(diff).toContainText("+print('hello')");

    // A leading `cd` is shown as where the command ran, not as part of the command.
    const run = steps.filter({ hasText: "pytest -q" });
    await expect(run.locator("code")).toHaveText("pytest -q");
    await expect(run.getByTestId("step-where")).toHaveText("in api");
    await run.click();
    await expect(page.getByText("1 failed, 12 passed").first()).toBeVisible();
  });

  test("sidebar footer links do not overlap", async ({ page }) => {
    await page.goto("/");
    const missions = await page.getByTestId("sidebar-missions-link").boundingBox();
    const settings = await page.getByTestId("sidebar-settings-link").boundingBox();
    expect(missions && settings).toBeTruthy();
    expect(missions!.x + missions!.width).toBeLessThanOrEqual(settings!.x + 1);
  });
});

test.describe("Chat layout on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("opens on the conversation, with the session list as a drawer", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("instruction-input")).toBeVisible();
    await expect(page.getByTestId("session-sidebar")).toHaveCount(0);

    await page.getByTitle("Expand Sessions Sidebar").click();
    await expect(page.getByTestId("session-sidebar")).toBeVisible();
    await page.getByTestId("new-conversation-btn").click();
    await expect(page.getByTestId("session-sidebar")).toHaveCount(0);
  });

  test("controls are comfortable to tap", async ({ page }) => {
    await page.goto("/");
    for (const name of [/Expand Right Panel/, /Expand Sessions Sidebar/]) {
      const box = await page.getByRole("button", { name }).boundingBox();
      expect(box).not.toBeNull();
      expect(Math.min(box!.width, box!.height)).toBeGreaterThanOrEqual(32);
    }
  });
});

test.describe("Chat layout on an iPhone", () => {
  // Real mobile emulation (touch, mobile viewport rules, tall empty state): a plain narrow
  // window did not reproduce the header being squeezed away.
  test.use({ viewport: { width: 402, height: 874 }, isMobile: true, hasTouch: true });

  test("the header keeps its height and the sidebar can be opened by touch", async ({ page }) => {
    await page.goto("/chat/draft");
    await expect(page.getByTestId("cockpit-empty-state")).toBeVisible();

    await expect(page.getByRole("heading", { name: "What should we work on?" })).toBeInViewport();
    const menu = page.getByRole("button", { name: "Expand Sessions Sidebar" });
    await expect(menu).toBeVisible();
    const box = await menu.boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.height).toBeGreaterThanOrEqual(32);
    const header = await page.locator("header").first().boundingBox();
    expect(header!.height).toBeGreaterThanOrEqual(48);

    const composer = await page.getByTestId("instruction-input").boundingBox();
    expect(composer!.y + composer!.height).toBeLessThanOrEqual(874);

    await menu.tap();
    await expect(page.getByTestId("session-sidebar")).toBeVisible();
    await expect(page.getByTestId("new-conversation-btn")).toBeInViewport();
  });

  test("the right panel (inspector) can be opened by touch", async ({ page }) => {
    await page.goto("/chat/draft");
    await page.getByRole("button", { name: /Expand Right Panel/ }).tap();
    await expect(page.getByRole("button", { name: "Close Inspector" })).toBeVisible();
  });
});

test.describe("Long content on an iPhone", () => {
  test.use({ viewport: { width: 402, height: 874 }, isMobile: true, hasTouch: true });

  test("wide code, long inline code and tables never make the chat scroll sideways", async ({ page, request }) => {
    const created = await request.post(`${BACKEND}/v1/sessions`, { data: { title: "Wide content" } });
    const { session_id: sessionId } = await created.json();
    const wide = "x".repeat(160);
    await request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, {
      data: {
        messages: [
          { role: "user", content: `Show me something wide ${wide}` },
          {
            role: "assistant",
            content: [
              "A reasonable place to look is `birotaio/mongodb-gcs-backup-with-a-really-long-repository-name-that-cannot-wrap` today.",
              "",
              "```bash",
              `docker compose up --build --force-recreate --remove-orphans ${wide}`,
              "```",
              "",
              "| Repository | Language | Description of what it does in detail |",
              "| --- | --- | --- |",
              `| birotaio/mongodb-gcs-backup | Shell | ${wide} |`,
              "",
              `https://github.com/birotaio/backend-services/blob/main/${wide}`,
            ].join("\\n"),
          },
        ],
      },
    });
    await page.goto(`/chat/${sessionId}`);
    await expect(page.getByTestId("flight-log-stream")).toBeVisible();

    const metrics = await page.evaluate(() => {
      const stream = document.querySelector('[data-testid="flight-log-stream"]') as HTMLElement;
      return {
        streamOverflow: stream.scrollWidth - stream.clientWidth,
        pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
        pageScrollable: document.documentElement.scrollHeight - window.innerHeight,
      };
    });
    expect(metrics.streamOverflow).toBeLessThanOrEqual(1);
    expect(metrics.pageOverflow).toBeLessThanOrEqual(0);
    // The app fills the screen; the document itself must not scroll (it would hide the top bar).
    expect(metrics.pageScrollable).toBeLessThanOrEqual(0);

    // Auto-scroll must move the stream only: scrolling the page root hides the top bar.
    await page.waitForTimeout(800);
    const rootScroll = await page.evaluate(() => {
      const root = document.body.firstElementChild?.nextElementSibling ?? document.body.firstElementChild;
      const all = Array.from(document.querySelectorAll("body *")).filter(
        (el) => (el as HTMLElement).scrollTop > 0 && getComputedStyle(el).overflowY === "hidden"
      );
      return { root: root ? (root as HTMLElement).scrollTop : 0, hiddenButScrolled: all.length };
    });
    expect(rootScroll.hiddenButScrolled).toBe(0);
    const header = await page.locator("header").first().boundingBox();
    expect(header!.y).toBeGreaterThanOrEqual(0);
    const composer = await page.getByTestId("instruction-input").boundingBox();
    expect(composer!.y + composer!.height).toBeLessThanOrEqual(874);
  });
});

test("an unsent message survives a reload", async ({ page, request }) => {
  const created = await request.post(`${BACKEND}/v1/sessions`, { data: { title: "Draft keeper" } });
  const { session_id: sessionId } = await created.json();
  await page.goto(`/chat/${sessionId}`);
  const input = page.getByTestId("instruction-input");
  await input.fill("which of the three options should we pick?");
  await page.waitForTimeout(500);

  await page.reload();
  await expect(page.getByTestId("instruction-input")).toHaveValue("which of the three options should we pick?");

  await page.getByTestId("instruction-input").fill("");
  await page.waitForTimeout(500);
  await page.reload();
  await expect(page.getByTestId("instruction-input")).toHaveValue("");
});

test.describe("Answer width on an iPhone", () => {
  test.use({ viewport: { width: 402, height: 874 }, isMobile: true, hasTouch: true });

  test("answers and activity use the full width, with no avatar column down the thread", async ({ page, request }) => {
    const sessionId = await seedSession(request);
    await page.goto(`/chat/${sessionId}`);

    const answerText = page.getByText("The greeting is fixed; one unrelated test still fails.");
    await expect(answerText).toBeVisible();
    await expect
      .poll(async () => (await answerText.boundingBox())?.x ?? 999, { timeout: 5000 })
      .toBeLessThanOrEqual(20);
    expect((await answerText.boundingBox())!.width).toBeGreaterThanOrEqual(402 - 2 * 20);

    const summary = page.getByTestId("action-group-toggle").first();
    await expect.poll(async () => (await summary.boundingBox())?.x ?? 999).toBeLessThanOrEqual(24);
  });
});
