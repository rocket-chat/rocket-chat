import { test, expect } from "@playwright/test";

/**
 * The deterministic E2E backend runs with `TEST_DATABASE_URL=""`, so durable memory is
 * unavailable and `GET /v1/memories` would answer 503. This spec stubs the API to exercise
 * the inspector's rendering and its "forget" action; the
 * pgvector search and tenant scoping are covered by `tests/integration/test_memory_recall.py`.
 */
test.describe("Cockpit Memory Inspector", () => {
  test("lists long-term memories and forgets one", async ({ page, request }) => {
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: { title: "Memory Inspector Mission" },
    });
    expect(createRes.ok()).toBeTruthy();
    const sessionId: string = (await createRes.json()).session_id;

    await page.route(/\/v1\/memories(\?.*)?$/, async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total: 2,
          memories: [
            {
              memory_id: "mem-1",
              scope: "org",
              kind: "fact",
              content: "Production deploys run at 09:00 UTC.",
              confidence: 1.0,
              source_session_id: sessionId,
              created_at: 1_700_000_000,
              last_used_at: null,
            },
            {
              memory_id: "mem-2",
              scope: "user",
              kind: "preference",
              content: "The team prefers trunk-based development.",
              confidence: 0.8,
              source_session_id: null,
              created_at: 1_700_000_100,
              last_used_at: 1_700_000_200,
            },
          ],
        }),
      });
    });

    await page.route(/\/v1\/memories\/[^/?]+$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ forgotten: true, memory_id: "mem-1" }),
      });
    });

    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    const trigger = page.getByTestId("memory-inspector-trigger");
    await expect(trigger).toBeVisible();
    await trigger.click();

    const inspector = page.getByTestId("memory-inspector");
    await expect(inspector).toBeVisible();

    const items = inspector.getByTestId("memory-item");
    await expect(items).toHaveCount(2);
    await expect(inspector.getByText("Production deploys run at 09:00 UTC.")).toBeVisible();
    await expect(inspector.getByText("The team prefers trunk-based development.")).toBeVisible();

    const orgMemory = inspector
      .getByTestId("memory-item")
      .filter({ hasText: "Production deploys run at 09:00 UTC." });
    await expect(orgMemory.getByTestId("memory-scope")).toHaveText(/org/i);

    // Forgetting is optimistic: the entry disappears without a reload.
    await orgMemory.getByTestId("memory-forget").click();
    await expect(inspector.getByTestId("memory-item")).toHaveCount(1);

    await page.getByLabel("Close memory inspector").click();
    await expect(inspector).toHaveCount(0);
  });
});
