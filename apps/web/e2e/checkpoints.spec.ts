import { test, expect } from "@playwright/test";

/** The e2e backend has no git sandbox, so the checkpoint API is stubbed; the API itself is covered by tests/unit/test_checkpoint_api.py. */
test("lists checkpoints and rewinds after a confirmation", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Checkpoint Mission" },
  });
  const sessionId: string = (await created.json()).session_id;

  const rewinds: unknown[] = [];
  await page.route(/\/v1\/sessions\/[^/]+\/turns/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session_id: sessionId, total: 0, limit: 50, offset: 0, turns: [] }),
    }),
  );
  await page.route(/\/v1\/sessions\/[^/]+\/checkpoints\/rewind/, async (route) => {
    rewinds.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.route(/\/v1\/sessions\/[^/]+\/checkpoints$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        available: true,
        checkpoints: [
          { name: "rocket-checkpoint-2-turn", sha: "bbb2222", created_at: 1_700_000_200 },
          { name: "rocket-checkpoint-1", sha: "aaa1111", created_at: 1_700_000_100 },
        ],
      }),
    }),
  );

  await page.goto(`/chat/${sessionId}`);
  await page.getByTestId("view-menu-trigger").click();
  await page.getByTestId("turn-inspector-toggle").click();
  const items = page.getByTestId("checkpoint-item");
  await expect(items).toHaveCount(2);

  await items.nth(1).getByTestId("checkpoint-rewind").click();
  expect(rewinds).toHaveLength(0); // nothing happens until confirmed
  await page.getByTestId("checkpoint-confirm").click();

  await expect(page.getByText("Workspace restored.")).toBeVisible();
  expect(rewinds).toEqual([{ reference: "rocket-checkpoint-1", mode: "files" }]);
});
