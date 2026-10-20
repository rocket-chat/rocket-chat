import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

/**
 * A command's output reaches the cockpit while the command runs: under its step in the
 * conversation (and in the Agent output terminal), and the step's final result replaces it.
 */
test("a running command shows what it prints before it finishes", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Live Output Mission" },
  });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);

  await page.goto(`/chat/${sessionId}`);
  await socket.ready;

  socket.emit("status_changed", { status: "running" });
  socket.emit("tool_started", {
    id: "call_live_1",
    name: "bash_exec",
    arguments: { command: "make build", cwd: "/workspace/app" },
  });
  socket.emit("log_chunk", { text: "compiling module a\r\n", tool_call_id: "call_live_1" });
  socket.emit("log_chunk", { text: "\u001b[32mcompiling module b\u001b[0m\r\n", tool_call_id: "call_live_1" });

  const live = page.getByTestId("tool-live-output");
  await expect(live).toBeVisible({ timeout: 10000 });
  await expect(live).toContainText("compiling module a");
  await expect(live).toContainText("compiling module b");
  await expect(live).not.toContainText("[32m");

  socket.emit("tool_completed", {
    id: "call_live_1",
    name: "bash_exec",
    arguments: { command: "make build", cwd: "/workspace/app" },
    result: "compiling module a\ncompiling module b\nBuild finished",
    status: "completed",
    duration_ms: 1200,
  });

  await expect(page.getByTestId("tool-live-output")).toHaveCount(0);
});
