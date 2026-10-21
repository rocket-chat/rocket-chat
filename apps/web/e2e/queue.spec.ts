import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

/**
 * While a turn runs, Alt+Enter (or Queue) keeps a message for after the turn instead of steering
 * it; it waits above the composer, can be withdrawn, and enters the conversation when its own
 * turn takes it.
 */
test.describe("Queued messages", () => {
  test("a queued message waits, can be withdrawn, and runs after the turn", async ({ page, request }) => {
    const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "Queue" } });
    const sessionId: string = (await created.json()).session_id;
    const socket = await routeSessionSocket(page, sessionId, { intercept: ["turn", "cancel_message"] });

    await page.goto(`/chat/${sessionId}`);
    await socket.ready;
    socket.emit("status_changed", { status: "running" });
    await expect(page.getByTestId("queue-button")).toBeVisible({ timeout: 10000 });

    const input = page.getByTestId("instruction-input");
    await input.fill("Then update the changelog");
    await input.press("Alt+Enter");
    await expect(page.getByTestId("pending-message-queue")).toContainText("after this turn");
    await expect.poll(() => socket.sent.filter((frame) => frame.type === "turn").length).toBe(1);
    const first = socket.sent.find((frame) => frame.type === "turn") as { message_id?: string };
    expect(JSON.stringify(first)).toContain('"delivery":"queue"');

    await input.fill("And bump the version");
    await page.getByTestId("queue-button").click();
    await expect(page.getByTestId("pending-message-queue")).toHaveCount(2);

    // Withdrawn before any turn took it: gone, and the server is told.
    await page.getByRole("button", { name: "Withdraw the message: And bump the version" }).click();
    await expect(page.getByTestId("pending-message-queue")).toHaveCount(1);
    await expect.poll(() => socket.sent.filter((frame) => frame.type === "cancel_message").length).toBe(1);

    // Its own turn takes the first one: it enters the conversation, once.
    socket.emit("user_message", { message_id: first.message_id ?? "q1", content: "Then update the changelog" });
    await expect(page.getByTestId("pending-message-queue")).toHaveCount(0);
    await expect(page.getByText("Then update the changelog", { exact: true })).toHaveCount(1);
  });
});
