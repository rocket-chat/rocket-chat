import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

test("the task checklist follows the plan the agent sends", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Checklist Mission" },
  });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);

  await page.goto(`/chat/${sessionId}`);
  await socket.ready;

  socket.emit("plan_updated", {
    tasks: [
      { id: "t1", title: "Read the failing test", status: "completed" },
      { id: "t2", title: "Fix the parser", status: "in_progress" },
      { id: "t3", title: "Run the suite", status: "pending" },
    ],
  });

  const checklist = page.getByTestId("task-checklist");
  await expect(checklist).toBeVisible({ timeout: 10000 });
  await expect(checklist).toContainText("1/3");
  await expect(checklist).toContainText("Fix the parser");

  socket.emit("plan_updated", {
    tasks: [
      { id: "t1", title: "Read the failing test", status: "completed" },
      { id: "t2", title: "Fix the parser", status: "completed" },
      { id: "t3", title: "Run the suite", status: "completed" },
    ],
  });
  await expect(checklist).toContainText("3/3");
});

test("a CI wait is one inline card with the pull request and settles in place", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Wait Mission" },
  });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);

  await page.goto(`/chat/${sessionId}`);
  await socket.ready;

  socket.emit("wait_started", {
    wait_id: "wait-1",
    wait_type: "ci_status",
    target: "acme/api#42",
    timeout_seconds: 1800,
    reason: "ci_status on acme/api#42",
  });
  const card = page.getByTestId("waiting-card");
  await expect(card).toHaveCount(1, { timeout: 10000 });
  await expect(card).toHaveAttribute("data-wait-status", "waiting");
  await expect(card).toContainText("Waiting for CI checks");
  await expect(card).toContainText("acme/api#42");
  await expect(card.getByTestId("wait-open-pr")).toHaveAttribute("href", "https://github.com/acme/api/pull/42");
  await expect(card.getByTestId("wait-detail")).toContainText("up to 30 min");

  socket.emit("github_event", {
    kind: "check_run",
    repo: "acme/api",
    pr_number: 42,
    summary: "2 of 3 checks passed",
    outcome: "pending",
    url: "https://github.com/acme/api/pull/42",
    actor: "ci",
    at: 1,
    woke: false,
  });
  await expect(card.getByTestId("wait-detail")).toContainText("2 of 3 checks passed");

  socket.emit("wait_completed", {
    wait_id: "wait-1",
    wait_type: "ci_status",
    target: "acme/api#42",
    status: "completed",
    summary: "CI finished green on acme/api#42",
  });
  await expect(card).toHaveAttribute("data-wait-status", "done", { timeout: 10000 });
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("wait-detail")).toContainText("CI finished green on acme/api#42");
});

test("a wait that timed out says so on its own card", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "Timeout Mission" } });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await socket.ready;

  socket.emit("wait_started", { wait_id: "wait-t", wait_type: "time", target: "30.0", timeout_seconds: 30 });
  const card = page.getByTestId("waiting-card");
  await expect(card).toContainText("Waiting for a timer", { timeout: 10000 });
  await expect(card.getByTestId("wait-open-pr")).toHaveCount(0);

  socket.emit("wait_completed", {
    wait_id: "wait-t",
    wait_type: "time",
    target: "30.0",
    status: "timeout",
    summary: "Wait timeout of 30s expired for time on 30.0.",
  });
  await expect(card).toHaveAttribute("data-wait-status", "timeout", { timeout: 10000 });
  await expect(card).toContainText("Gave up waiting");
});
