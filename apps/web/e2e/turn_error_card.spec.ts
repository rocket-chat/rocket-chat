import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

const API = "http://localhost:8000";

/** A failed turn is a card where it happened, with Retry, another model, and the setting to fix. */
test("a provider failure offers retry, another model and the model settings", async ({ page, request }) => {
  const created = await request.post(`${API}/v1/sessions`, { data: { title: "Failure" } });
  const sessionId: string = (await created.json()).session_id;
  await request.post(`${API}/v1/dev/sessions/${sessionId}/transcript`, {
    data: { messages: [{ role: "user", content: "Summarise the repository" }] },
  });
  const socket = await routeSessionSocket(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await socket.ready;
  await expect(page.getByTestId("flight-log-stream")).toContainText("Summarise the repository");

  socket.emit("error", { error: "AuthenticationError: invalid api key", error_kind: "provider" });
  const card = page.getByTestId("turn-error-card");
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute("data-error-kind", "provider");
  await expect(card.getByTestId("turn-error-settings")).toHaveAttribute("href", "/settings/org/models");

  await card.getByTestId("turn-error-retry").click();
  await expect
    .poll(() => socket.sent.some((frame) => JSON.stringify(frame).includes("Summarise the repository")))
    .toBe(true);
});

test("an internal failure offers retry but no settings", async ({ page, request }) => {
  const created = await request.post(`${API}/v1/sessions`, { data: { title: "Internal" } });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await socket.ready;

  socket.emit("error", { error: "KeyError: 'session'", error_kind: "internal" });
  const card = page.getByTestId("turn-error-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("turn-error-settings")).toHaveCount(0);
});
