import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

const API = "http://localhost:8000";

/** A conversation in a background tab says it needs its person, or is done, in the title and a notification. */
test("the tab title and a notification say when a background turn needs you or is done", async ({ page, request, context }) => {
  await context.grantPermissions(["notifications"]);
  await page.addInitScript(() => {
    const shown: string[] = [];
    (window as unknown as { shownNotifications: string[] }).shownNotifications = shown;
    class FakeNotification {
      static permission = "granted";
      static requestPermission = async () => "granted";
      constructor(title: string) {
        shown.push(title);
      }
    }
    Object.defineProperty(window, "Notification", { value: FakeNotification });
    // The tab is in the background for the whole test.
    Object.defineProperty(document, "hidden", { get: () => true });
  });
  await request.put(`${API}/v1/settings/user_preferences`, { data: { overrides: { turn_notifications: true } } });

  try {
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Attention" } });
    const sessionId: string = (await created.json()).session_id;
    const socket = await routeSessionSocket(page, sessionId);
    await page.goto(`/chat/${sessionId}`);
    await socket.ready;

    socket.emit("status_changed", { status: "running", message: "Working" });
    socket.emit("approval_required", { action_id: "a1", action_type: "bash_exec", description: "ls", risk_level: "low" });
    await expect(page).toHaveTitle(/^\(!\) Needs you/);

    await page.getByTestId("approval-approve").click();
    socket.emit("status_changed", { status: "completed", message: "done", final: true });
    await expect(page).toHaveTitle(/^✓ Done/);

    const shown = await page.evaluate(() => (window as unknown as { shownNotifications: string[] }).shownNotifications);
    expect(shown).toEqual(["Rocket Chat needs you", "Rocket Chat is done"]);
  } finally {
    await request.delete(`${API}/v1/settings/user_preferences/turn_notifications`);
  }
});
