import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

const API = "http://localhost:8000";

async function openSession(page: import("@playwright/test").Page, request: import("@playwright/test").APIRequestContext) {
  const created = await request.post(`${API}/v1/sessions`, { data: { title: "Screen mission" } });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await socket.ready;
  return { sessionId, socket };
}

test.describe("Session screen", () => {
  test("the secondary views live in one labelled menu", async ({ page, request }) => {
    await openSession(page, request);
    await expect(page.getByTestId("share-trigger")).toContainText("Share");
    await page.getByTestId("view-menu-trigger").click();
    const menu = page.getByRole("menu");
    for (const name of ["Artifacts", "Turn history", "Memory", "Settings"]) {
      await expect(menu.getByRole("menuitem", { name })).toBeVisible();
    }
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
  });

  test("the inspector opens by itself on the first change and its tabs carry counts", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    await expect(page.getByTestId("tab-changes")).toHaveCount(0);

    socket.emit("file_diff", { path: "src/a.py", diff_content: "@@ -1 +1 @@\n-a\n+b\n", additions: 1, deletions: 1, is_new_file: false });
    socket.emit("file_diff", { path: "src/b.py", diff_content: "@@ -1 +1 @@\n-a\n+b\n", additions: 1, deletions: 1, is_new_file: false });
    await expect(page.getByTestId("tab-changes")).toBeVisible();
    await expect(page.getByTestId("tab-changes-count")).toHaveText("2");

    socket.emit("process_started", { process_id: "p1", name: "dev", command: "npm run dev", state: "running", port: 3000 });
    await expect(page.getByTestId("tab-processes-count")).toHaveText("1");
    socket.emit("process_exited", { process_id: "p1", name: "dev", command: "npm run dev", state: "exited", exit_code: 0 });
    await expect(page.getByTestId("tab-processes-count")).toHaveCount(0);
  });

  test("the terminal is folded until something prints", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    await page.getByTestId("right-panel-toggle").click();
    await expect(page.getByTestId("terminal-toggle")).toHaveAttribute("aria-expanded", "false");

    socket.emit("log_chunk", { text: "hello from a command\r\n" });
    await expect(page.getByTestId("terminal-toggle")).toHaveAttribute("aria-expanded", "true");
    await page.getByTestId("terminal-toggle").click();
    await expect(page.getByTestId("terminal-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  test("an approval waits above the composer and Enter in the card approves it", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    socket.emit("approval_required", { action_id: "a1", action_type: "bash_exec", description: "rm -rf build", risk_level: "high" });

    const dock = page.getByTestId("gate-dock");
    await expect(dock.getByTestId("approval-card")).toBeVisible();
    await expect(dock).toContainText("Approve bash_exec?");
    await expect(dock).toContainText("High risk");

    // Focus elsewhere on the page: Enter is not an answer.
    await page.getByTestId("flight-log-stream").click();
    await page.keyboard.press("Enter");
    await expect(dock.getByTestId("approval-card")).toBeVisible();
    expect(socket.sent.some((frame) => JSON.stringify(frame).includes("a1"))).toBe(false);

    await dock.getByTestId("approval-card").focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => socket.sent.some((frame) => JSON.stringify(frame).includes("a1"))).toBe(true);
    await expect(dock).toHaveCount(0);
  });

  test("an irreversible action is approved only once the word is typed", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    socket.emit("approval_required", { action_id: "a3", action_type: "git_revert", description: "hard reset", risk_level: "critical" });
    const card = page.getByTestId("approval-card");
    await expect(card).toBeVisible();
    await expect(card.getByTestId("approval-approve")).toBeDisabled();

    await card.focus();
    await page.keyboard.press("Enter");
    expect(socket.sent.some((frame) => JSON.stringify(frame).includes("a3"))).toBe(false);

    await card.getByTestId("approval-confirm").fill("approve");
    await card.getByTestId("approval-approve").click();
    await expect.poll(() => socket.sent.some((frame) => JSON.stringify(frame).includes("a3"))).toBe(true);
  });

  test("a subagent's live text and its failed outcome are shown as sent", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    socket.emit("subagent_started", { subagent_id: "sa1", role: "explorer", task: "find the retry path" });
    socket.emit("subagent_progress", { subagent_id: "sa1", delta: "Looking in ", is_reasoning: false });
    socket.emit("subagent_progress", { subagent_id: "sa1", delta: "api/turns.", is_reasoning: false });

    // Delegations fold into one group, opened to see the step.
    await page.getByRole("button", { name: /Delegated to 1 specialist/ }).click();
    const row = page.getByTestId("activity-step").and(page.getByTitle("Delegated to explorer: find the retry path"));
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.getByText("Looking in api/turns.")).toBeVisible();

    socket.emit("subagent_completed", {
      subagent_id: "sa1",
      role: "explorer",
      task: "find the retry path",
      summary: "Gave up: the repository is not cloned.",
      status: "failed",
    });
    await expect(page.getByText("Gave up: the repository is not cloned.")).toBeVisible();
    await expect(page.getByTestId("subagent-status")).toHaveText("Failed");
  });

  test("typing in the composer keeps Enter and Escape for the composer", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    socket.emit("approval_required", { action_id: "a2", action_type: "bash_exec", description: "ls", risk_level: "low" });
    await expect(page.getByTestId("approval-card")).toBeVisible();

    await page.getByTestId("instruction-input").click();
    await page.keyboard.press("Escape");
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("approval-card")).toBeVisible();
    expect(socket.sent.some((frame) => JSON.stringify(frame).includes("a2"))).toBe(false);
  });

  test("a finished turn that edited files offers to review them", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("instruction-input").fill("[stub:write] save a note");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });

    const summary = page.getByTestId("turn-summary");
    await expect(summary).toContainText("Edited 1 file", { timeout: 20000 });
    await page.getByTestId("turn-summary-review").click();
    await expect(page.getByTestId("tab-changes")).toHaveAttribute("aria-selected", "true");
  });

  test("the task list folds to its current task", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    socket.emit("plan_updated", {
      tasks: [
        { id: "t1", title: "Read the test", status: "completed" },
        { id: "t2", title: "Fix the parser", status: "in_progress" },
      ],
    });
    await expect(page.getByTestId("task-checklist")).toContainText("Now: Fix the parser");

    socket.emit("plan_updated", {
      tasks: [
        { id: "t1", title: "Read the test", status: "completed" },
        { id: "t2", title: "Fix the parser", status: "completed" },
      ],
    });
    await expect(page.getByTestId("task-checklist")).toContainText("All done");
    await expect(page.getByTestId("task-checklist")).not.toHaveAttribute("open", "");
  });
});

test.describe("Session screen on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("nothing scrolls sideways and the composer stays reachable", async ({ page, request }) => {
    await openSession(page, request);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expect(page.getByTestId("instruction-input")).toBeInViewport();
    await expect(page.getByTestId("view-menu-trigger")).toBeVisible();
  });
});

test.describe("The right panel keeps the person's choice", () => {
  const change = { path: "src/a.py", diff_content: "@@ -1 +1 @@\n-a\n+b\n", additions: 1, deletions: 1, is_new_file: false };

  test("a panel the person closed is not opened again by the next change", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    await page.getByTestId("right-panel-toggle").click();
    await expect(page.getByTestId("terminal-toggle")).toBeVisible();
    await page.getByTestId("right-panel-toggle").click();
    await expect(page.getByTestId("terminal-toggle")).toHaveCount(0);

    socket.emit("file_diff", change);
    socket.emit("process_started", { process_id: "p1", name: "dev", command: "npm run dev", state: "running", port: 3000 });
    // The change is counted, but nothing slides in: the panel stays closed.
    await expect(page.getByTestId("tab-changes")).toHaveCount(0);
    await expect(page.getByTestId("terminal-toggle")).toHaveCount(0);

    // And the choice outlives the page: a new session does not reopen it either.
    const { socket: next } = await openSession(page, request);
    next.emit("file_diff", change);
    await expect(page.getByTestId("tab-changes")).toHaveCount(0);
  });

  test("a panel nobody chose about still opens once on the first change", async ({ page, request }) => {
    const { socket } = await openSession(page, request);
    socket.emit("file_diff", change);
    await expect(page.getByTestId("tab-changes")).toBeVisible({ timeout: 10000 });
  });
});
