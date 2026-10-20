import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

const API = "http://localhost:8000";

async function newSession(request: import("@playwright/test").APIRequestContext, title: string): Promise<string> {
  const created = await request.post(`${API}/v1/sessions`, { data: { title } });
  return (await created.json()).session_id;
}

test.describe("Session sidebar", () => {
  test("conversations are grouped by when they moved and can be searched", async ({ page, request }) => {
    const alpha = await newSession(request, "Alpha refund bug");
    await newSession(request, "Beta migration plan");

    await page.goto(`/chat/${alpha}`);
    const sidebar = page.getByTestId("session-sidebar");
    await expect(sidebar.getByRole("heading", { name: /^Today/ })).toBeVisible();

    await page.getByTestId("session-search").fill("refund");
    await expect(sidebar.getByTestId("session-row").filter({ hasText: "Alpha refund bug" })).toBeVisible();
    await expect(sidebar.getByText("Beta migration plan")).toHaveCount(0);

    await page.getByTestId("session-search").fill("nothing matches this");
    await expect(sidebar).toContainText("No conversation matches");
  });

  test("a session that waits on a person is surfaced and marked", async ({ page, request }) => {
    const waiting = await newSession(request, "Needs an answer");
    const socket = await routeSessionSocket(page, waiting);

    await page.goto(`/chat/${waiting}`);
    await socket.ready;
    const sidebar = page.getByTestId("session-sidebar");
    // The live update patches the listed session; wait for the listing so a slower
    // first fetch cannot replace the patched row afterwards.
    await expect(sidebar.getByTestId("session-row").filter({ hasText: "Needs an answer" })).toBeVisible();
    socket.emit("session_updated", { session_id: waiting, awaiting_input: "approval" });

    await expect(sidebar.getByRole("heading", { name: /^Needs you/ })).toBeVisible();
    await expect(sidebar.getByTestId("session-status").first()).toHaveAttribute("data-status", "needs_you");
    await expect(page.getByTestId("status-chip")).toHaveAttribute("data-status", "needs_you");

    socket.emit("session_updated", { session_id: waiting, awaiting_input: "" });
    await expect(sidebar.getByRole("heading", { name: /^Needs you/ })).toHaveCount(0);
  });

  test("a failed turn shows as failed", async ({ page, request }) => {
    const failed = await newSession(request, "Broke");
    const socket = await routeSessionSocket(page, failed);

    await page.goto(`/chat/${failed}`);
    await socket.ready;
    const sidebar = page.getByTestId("session-sidebar");
    await expect(sidebar.getByTestId("session-row").filter({ hasText: "Broke" })).toBeVisible();
    socket.emit("session_updated", { session_id: failed, last_turn_status: "error" });

    await expect(sidebar.getByTestId("session-status").first()).toHaveAttribute("data-status", "failed");
  });

  test("collapsing leaves a rail with the essentials, and the context menu has the row's actions", async ({ page, request }) => {
    const id = await newSession(request, "Menu target");
    await page.goto(`/chat/${id}`);

    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(page.getByTestId("session-sidebar")).toHaveCount(0);
    const rail = page.getByTestId("session-rail");
    await expect(rail).toBeVisible();
    await rail.getByRole("button", { name: "New conversation" }).click();
    await expect(page).toHaveURL(/\/chat\/draft/);
    await rail.getByRole("button", { name: "Open conversations" }).click();

    const row = page.locator(`[data-testid="session-row"][data-session-id="${id}"]`);
    await row.click({ button: "right" });
    const menu = page.getByTestId("session-context-menu");
    await expect(menu).toContainText("Pin");
    await expect(menu).toContainText("Copy link");
    await menu.getByRole("menuitem", { name: /Pin/ }).click();
    await expect(page.locator(`[data-testid="session-row"][data-session-id="${id}"]`)).toHaveAttribute("data-pinned", "true");
  });
});
