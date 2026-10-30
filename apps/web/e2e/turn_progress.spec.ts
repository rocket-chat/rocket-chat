import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

const API = "http://localhost:8000";

/** A long turn says how it is going: elapsed time, step, the tool running now, and the model. */
test("a running turn shows its step, its current tool and the model running it", async ({ page, request }) => {
  const created = await request.post(`${API}/v1/sessions`, { data: { title: "Progress" } });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await socket.ready;

  socket.emit("status_changed", { status: "running", message: "Analyzing", model: "anthropic/claude-sonnet-5-5", agent_name: "", step: 3 });
  socket.emit("tool_started", { id: "t1", name: "bash_exec", arguments: { command: "make test" } });

  const progress = page.getByTestId("turn-progress");
  await expect(progress).toContainText("step 3");
  await expect(progress).toContainText("claude-sonnet-5-5");
  await expect(progress).toContainText(/\d+ s/);
  // The header names the model running the turn, not the composer's choice.
  await expect(page.locator("header")).toContainText("claude-sonnet-5-5");

  socket.emit("tool_completed", { id: "t1", name: "bash_exec", arguments: { command: "make test" }, result: "ok", duration_ms: 125000 });
  socket.emit("status_changed", { status: "completed", message: "done", final: true });
  await expect(progress).toHaveCount(0);
  // Finished steps fold into their group; opened, each says how long it took.
  await page.getByRole("button", { name: "Ran 1 command" }).click();
  await expect(page.getByTestId("step-duration").first()).toHaveText("2 m 05 s");
});
