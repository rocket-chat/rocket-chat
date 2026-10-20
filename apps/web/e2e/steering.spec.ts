import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

/**
 * While a turn runs, the composer sends the message to it instead of waiting: the cockpit shows
 * the message at once, the running turn is left as it is, and the backend's `user_message`
 * broadcast does not show it a second time.
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

  await expect(page.getByText("Use the staging database instead", { exact: true })).toHaveCount(1);
  await expect(page.getByTestId("stop-button")).toBeVisible();
  await expect.poll(() => socket.sent.filter((frame) => frame.type === "turn").length).toBe(1);
  const turn = socket.sent.find((frame) => frame.type === "turn");
  expect(JSON.stringify(turn)).toContain("Use the staging database instead");

  socket.emit("user_message", {
    message_id: (turn as { message_id?: string }).message_id ?? "m1",
    content: "Use the staging database instead",
  });
  await expect(page.getByText("Use the staging database instead", { exact: true })).toHaveCount(1);
});
