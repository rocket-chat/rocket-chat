import { test, expect } from "@playwright/test";

/** An agent can switch reasoning off, and the choice is stored as `none`. */
test.describe("Agent reasoning effort", () => {
  test("offers Off and persists it", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("Quick Decider");

    const effort = page.getByTestId("agent-reasoning-effort");
    await expect(effort.locator("option")).toHaveText([
      "Model / organization default",
      "Off (no reasoning, fastest)",
      "Minimal",
      "Low (faster, budget-friendly)",
      "Medium (balanced reasoning)",
      "High (deep architectural thinking)",
      "Extra high",
      "Max",
    ]);
    await effort.selectOption("none");
    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByText("SAVED!")).toBeVisible();

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === "Quick Decider");
    expect(saved.reasoning_effort).toBe("none");
  });
});
