import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

/**
 * While a turn runs, the composer sends the message to it instead of waiting: the message waits
 * above the composer ("delivered at the next step") until the agent takes it, then enters the
 * conversation where the agent read it, once; the running turn is left as it is.
 */
test("a message sent while the agent works steers the running turn", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Steering Mission" },
  });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);

  await page.goto(`/chat/${sessionId}`);
  await socket.ready;
  socket.emit("status_changed", { status: "running" });

  await expect(page.getByTestId("stop-button")).toBeVisible({ timeout: 10000 });
  await page.getByTestId("instruction-input").fill("Use the staging database instead");
  await page.getByTestId("steer-button").click();

  const pending = page.getByTestId("pending-message-steer");
  await expect(pending).toContainText("delivered at the next step");
  await expect(page.getByText("Use the staging database instead", { exact: true })).toHaveCount(1);
  await expect(page.getByTestId("stop-button")).toBeVisible();
  await expect.poll(() => socket.sent.filter((frame) => frame.type === "turn").length).toBe(1);
  const turn = socket.sent.find((frame) => frame.type === "turn");
  expect(JSON.stringify(turn)).toContain("Use the staging database instead");
  expect(JSON.stringify(turn)).toContain('"delivery":"steer"');

  socket.emit("user_message", {
    message_id: (turn as { message_id?: string }).message_id ?? "m1",
    content: "Use the staging database instead",
  });
  await expect(pending).toHaveCount(0);
  await expect(page.getByText("Use the staging database instead", { exact: true })).toHaveCount(1);
});
