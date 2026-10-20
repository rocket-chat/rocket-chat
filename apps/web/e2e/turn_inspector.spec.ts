import { test, expect } from "@playwright/test";

/**
 * The deterministic E2E backend runs with `TEST_DATABASE_URL=""`, so `GET
 * /v1/sessions/{id}/turns` answers 503 and there is no durable trace to read. This spec
 * therefore stubs the API response — it exercises the inspector's rendering and its
 * interrupted-turn retry affordance, which is the cockpit half of the contract. The
 * DB-backed persistence itself is covered by `tests/integration/test_turn_traces.py`.
 */
test.describe("Cockpit Turn Inspector", () => {
  test("lists persisted turns and offers retry only for an interrupted turn", async ({
    page,
    request,
  }) => {
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: { title: "Turn Inspector Mission" },
    });
    expect(createRes.ok()).toBeTruthy();
    const sessionId: string = (await createRes.json()).session_id;

    // Give the session a prior prompt so the retry affordance has something to re-run.
    await request.post(`http://localhost:8000/v1/dev/sessions/${sessionId}/transcript`, {
      data: { messages: [{ role: "user", content: "inspect the workspace" }] },
    });

    await page.route(/\/v1\/sessions\/[^/]+\/turns/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: sessionId,
          total: 3,
          limit: 50,
          offset: 0,
          turns: [
            {
              id: "trace-1",
              session_id: sessionId,
              turn_index: 1,
              started_at: 1_700_000_000,
              ended_at: 1_700_000_004,
              status: "completed",
              reasoning_summary: "Inspected the workspace and listed three files.",
              tool_calls: [{ name: "list_dir", status: "success" }],
              diff_paths: ["src/app.py"],
              attachments: [{ id: "att_1", name: "failing-test.log", mime: "text/plain", size: 2048 }],
              approvals: [],
              tokens: { total_tokens: 128 },
              error: null,
            },
            {
              id: "trace-2",
              session_id: sessionId,
              turn_index: 2,
              started_at: 1_700_000_010,
              ended_at: null,
              status: "interrupted",
              reasoning_summary: null,
              tool_calls: [],
              diff_paths: [],
              approvals: [],
              tokens: {},
              error: "process died mid-turn",
            },
            {
              id: "trace-3",
              session_id: sessionId,
              turn_index: 2,
              started_at: 1_700_000_020,
              ended_at: 1_700_000_030,
              status: "completed",
              reasoning_summary: null,
              tool_calls: [],
              diff_paths: [],
              approvals: [],
              tokens: {},
              error: null,
              resume_attempt: 1,
            },
          ],
        }),
      });
    });

    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    await page.getByTestId("view-menu-trigger").click();
    const toggle = page.getByTestId("turn-inspector-toggle");
    await expect(toggle).toBeVisible();
    await toggle.click();

    const inspector = page.getByTestId("turn-inspector");
    await expect(inspector).toBeVisible();

    const items = inspector.getByTestId("turn-inspector-item");
    await expect(items).toHaveCount(3);

    // The completed turn renders its status, tool call and reasoning summary.
    const completed = page
      .locator('[data-testid="turn-inspector-item"][data-turn-status="completed"]')
      .first();
    await expect(completed.getByTestId("turn-status")).toHaveText(/completed/i);
    await expect(completed.getByText("list_dir")).toBeVisible();
    // What the model was given is listed by reference: name and size, never the content.
    await expect(completed.getByTestId("turn-attachments")).toContainText("failing-test.log");
    await expect(completed.getByTestId("turn-attachments")).toContainText("2.0 KB");
    await completed.getByTestId("turn-reasoning-toggle").click();
    await expect(completed.getByText("Inspected the workspace and listed three files.")).toBeVisible();

    // The interrupted turn is flagged and carries the retry affordance; the completed
    // turn must not.
    const interrupted = page.locator(
      '[data-testid="turn-inspector-item"][data-turn-status="interrupted"]',
    );
    await expect(interrupted.getByTestId("turn-status")).toHaveText(/interrupted/i);
    await expect(interrupted.getByText("process died mid-turn")).toBeVisible();
    await expect(interrupted.getByTestId("turn-retry")).toBeVisible();
    await expect(interrupted.getByTestId("turn-retry")).toBeEnabled();

    await expect(inspector.getByTestId("turn-retry")).toHaveCount(1);

    // A task the platform continued after a restart says so.
    await expect(inspector.getByTestId("turn-resumed")).toHaveCount(1);

    // The slide-over can be dismissed again.
    await page.getByLabel("Close turn inspector").click();
    await expect(inspector).toHaveCount(0);
  });
});
