import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";

/**
 * Regenerates the screenshots in doc/assets/screenshots from a deterministic stack
 * (stub model, fake sandbox, seeded conversations). Skipped unless DOCS_SCREENSHOTS=1:
 *
 *     pnpm run docs:screenshots
 */
test.skip(!process.env.DOCS_SCREENSHOTS, "Only runs when regenerating the documentation images");

const BACKEND = "http://localhost:8000";
const OUT = path.resolve(__dirname, "../../../../doc/assets/screenshots");

test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

async function seedConversation(request: APIRequestContext): Promise<string> {
  const created = await request.post(`${BACKEND}/v1/sessions`, {
    data: { title: "Fix the refund rounding bug" },
  });
  const { session_id: sessionId } = await created.json();
  await request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, {
    data: {
      messages: [
        {
          role: "user",
          content:
            "Clone acme/billing-api, find why test_refund_rounding fails and fix it. Run the tests when you are done.",
        },
        {
          role: "assistant",
          content:
            "Fixed. `refund_amount` rounded each line item separately, so a refund of three items at €3.335 lost a cent.\n\nI now round the total once, in `billing/refunds.py`, and added a regression test. All 48 tests pass.\n\n- `billing/refunds.py`: round the sum, not the parts\n- `tests/test_refunds.py`: new case for three-way splits",
          reasoning:
            "The failing test compares a refund total to the cent. Rounding per line item accumulates error, so the fix is to round once at the end.",
          tool_calls: [
            {
              id: "c1",
              name: "clone_repository",
              arguments: { repository: "acme/billing-api" },
              result: "Cloned acme/billing-api into /workspace/billing-api",
              status: "success",
            },
            {
              id: "c2",
              name: "bash_exec",
              arguments: { command: "cd billing-api && uv run pytest tests/test_refunds.py -q" },
              result: "1 failed, 11 passed",
              status: "error",
            },
            {
              id: "c3",
              name: "file_edit",
              arguments: {
                path: "billing-api/billing/refunds.py",
                target_block: "return sum(round(item.amount, 2) for item in items)",
                replacement_block: "return round(sum(item.amount for item in items), 2)",
              },
              result: "ok",
              status: "success",
            },
            {
              id: "c4",
              name: "bash_exec",
              arguments: { command: "cd billing-api && uv run pytest -q" },
              result: "48 passed",
              status: "success",
            },
          ],
        },
      ],
    },
  });
  return sessionId as string;
}

async function stubProcesses(page: Page): Promise<void> {
  await page.route(/\/v1\/sessions\/[^/]+\/processes$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        sandbox_running: true,
        processes: [
          { process_id: "pa1b2c3", name: "dev server", command: "npm run dev -- --port 3000", state: "running", port: 3000, exit_code: null, started_at: 1, ended_at: null },
          { process_id: "pd4e5f6", name: "unit tests (watch)", command: "uv run pytest -f", state: "running", port: null, exit_code: null, started_at: 2, ended_at: null },
          { process_id: "p778899", name: "type check", command: "pnpm type-check", state: "exited", port: null, exit_code: 0, started_at: 3, ended_at: 4 },
        ],
      }),
    }),
  );
}

test("new conversation", async ({ page }) => {
  await page.goto("/chat/draft");
  await expect(page.getByTestId("cockpit-empty-state")).toBeVisible();
  await page.screenshot({ path: path.join(OUT, "new-conversation.png") });
});

test("a finished conversation", async ({ page, request }) => {
  const sessionId = await seedConversation(request);
  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByText("Fixed. ")).toBeVisible();
  await page.screenshot({ path: path.join(OUT, "conversation.png") });
});

test("the cockpit with the inspector", async ({ page, request }) => {
  const sessionId = await seedConversation(request);
  await stubProcesses(page);
  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByText("Fixed. ")).toBeVisible();
  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-processes").click();
  await expect(page.getByTestId("process-row")).toHaveCount(3);
  await page.screenshot({ path: path.join(OUT, "cockpit.png") });
  await page.screenshot({ path: path.join(OUT, "processes.png") });
});

test("the sandbox catalogue", async ({ page, request }) => {
  const name = "Devbox with browser";
  const created = await request.post(`${BACKEND}/v1/admin/sandboxes`, {
    data: {
      name,
      image: "registry.example.com/team/devbox-browser:1",
      description: "Chromium and Playwright, for checking the apps the agent builds.",
      capabilities: ["browser"],
    },
  });
  const id = (await created.json()).id as string;
  try {
    await page.goto("/settings/org/sandboxes");
    await expect(page.getByTestId("sandbox-row").filter({ hasText: name })).toBeVisible();
    await page.screenshot({ path: path.join(OUT, "sandboxes.png") });
  } finally {
    await request.delete(`${BACKEND}/v1/admin/sandboxes/${id}`);
  }
});

test("the settings console", async ({ page }) => {
  await page.goto("/settings/user/profile");
  await expect(page.getByRole("heading").first()).toBeVisible();
  await page.screenshot({ path: path.join(OUT, "settings.png") });
});
