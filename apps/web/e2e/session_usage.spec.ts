import { test, expect, type Page } from "@playwright/test";

const API = "http://localhost:8000";

const totals = (over: Record<string, number> = {}) => ({
  calls: 0,
  prompt_tokens: 0,
  cache_read_tokens: 0,
  cache_creation_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
  cost_usd: 0,
  own_key_cost_usd: 0,
  ...over,
});

const usage = (sessionId: string, cost: number) => ({
  session_id: sessionId,
  totals: totals({
    calls: 18,
    prompt_tokens: 650_000,
    cache_read_tokens: 600_000,
    completion_tokens: 9_000,
    total_tokens: 659_000,
    cost_usd: cost,
    own_key_cost_usd: 0.1,
  }),
  turns: [
    { turn_index: 1, started_at: 1, ended_at: 2, status: "completed", ...totals({ calls: 12, prompt_tokens: 400_000, completion_tokens: 6_000, cost_usd: 0.08 }) },
    { turn_index: 2, started_at: 3, ended_at: null, status: "running", ...totals({ calls: 6, prompt_tokens: 250_000, completion_tokens: 3_000, cost_usd: cost - 0.08 }) },
  ],
  models: [
    { model: "openrouter/deepseek/deepseek-v4-pro-0813", ...totals({ calls: 16, cost_usd: cost - 0.01 }) },
    { model: "openrouter/deepseek/deepseek-v4.1-flash", ...totals({ calls: 2, cost_usd: 0.01 }) },
  ],
  before_first_turn: totals(),
});

async function newSession(page: Page): Promise<string> {
  const created = await page.request.post(`${API}/v1/sessions`, { data: { title: "Usage" } });
  return (await created.json()).session_id;
}

test.describe("Session usage", () => {
  test("the header shows what the session cost and what it is made of", async ({ page }) => {
    const sessionId = await newSession(page);
    await page.route(`**/v1/sessions/${sessionId}/usage`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(usage(sessionId, 0.118)) })
    );
    await page.goto(`/chat/${sessionId}`);

    const chip = page.getByTestId("session-usage-chip");
    await expect(chip).toBeVisible();
    await expect(page.getByTestId("session-usage-cost")).toHaveText("$0.118");
    await expect(chip).toContainText("659k tok");

    await chip.click();
    const popover = page.getByTestId("session-usage-popover");
    await expect(popover).toBeVisible();
    await expect(page.getByTestId("session-usage-total")).toHaveText("$0.118");
    await expect(popover).toContainText("92%"); // 600k of 650k prompt tokens came from the cache
    await expect(popover).toContainText("$0.100 of it was paid with your own provider key");

    const rows = page.getByTestId("session-usage-turn");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("#1");
    await expect(rows.nth(0)).toContainText("$0.080");
    await expect(rows.nth(1)).toContainText("running");
    await expect(popover).toContainText("deepseek/deepseek-v4-pro-0813");

    await page.keyboard.press("Escape");
    await expect(popover).toHaveCount(0);
    await expect(chip).toHaveAttribute("aria-expanded", "false");
  });

  test("the figures refresh when a turn ends", async ({ page }) => {
    const sessionId = await newSession(page);
    let cost = 0.118;
    await page.route(`**/v1/sessions/${sessionId}/usage`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(usage(sessionId, cost)) })
    );
    await page.goto(`/chat/${sessionId}`);
    await expect(page.getByTestId("session-usage-cost")).toHaveText("$0.118");

    cost = 0.25;
    await page.getByTestId("instruction-input").fill("hello");
    await page.getByTestId("instruction-input").press("Enter");

    await expect(page.getByTestId("session-usage-cost")).toHaveText("$0.250", { timeout: 30000 });
  });

  test("a session that has not called a model shows nothing", async ({ page }) => {
    const sessionId = await newSession(page);
    await page.route(`**/v1/sessions/${sessionId}/usage`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: sessionId, totals: totals(), turns: [], models: [], before_first_turn: totals() }),
      })
    );

    await page.goto(`/chat/${sessionId}`);
    await expect(page.getByTestId("flight-log-stream").or(page.getByTestId("instruction-input")).first()).toBeVisible();

    await expect(page.getByTestId("session-usage-chip")).toHaveCount(0);
  });

  test("on a phone the chip keeps to the cost and the popover stays inside the window", async ({ page }) => {
    const sessionId = await newSession(page);
    await page.route(`**/v1/sessions/${sessionId}/usage`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(usage(sessionId, 0.118)) })
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/chat/${sessionId}`);

    const chip = page.getByTestId("session-usage-chip");
    await expect(chip).toBeVisible();
    await expect(page.getByTestId("session-usage-tokens")).toBeHidden();
    await chip.click();

    const box = await page.getByTestId("session-usage-popover").boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  });
});
