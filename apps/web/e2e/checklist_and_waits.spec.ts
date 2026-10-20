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

test("a durable wait is shown while the turn is paused and cleared when it ends", async ({ page, request }) => {
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
    reason: "Waiting for CI on acme/api#42",
  });
  await expect(page.getByTestId("waiting-card").first()).toContainText("Waiting for condition", {
    timeout: 10000,
  });

  socket.emit("wait_completed", {
    wait_id: "wait-1",
    wait_type: "ci_status",
    target: "acme/api#42",
    status: "completed",
    summary: "CI finished green on acme/api#42",
  });
  await expect(page.getByText("Wait completed: CI finished green on acme/api#42")).toBeVisible({
    timeout: 10000,
  });
});
