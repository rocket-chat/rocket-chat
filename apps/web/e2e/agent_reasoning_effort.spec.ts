import { test, expect } from "@playwright/test";

/** An agent can switch reasoning off, and the choice is stored as `none`. */
test.describe("Agent reasoning effort", () => {
  test("offers Off and persists it", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByTestId("agent-new").click();
    await page.getByTestId("agent-name").fill("Quick Decider");

    await page.getByTestId("agent-section-model").click();
    const effort = page.getByTestId("agent-reasoning-effort");
    await expect(effort.locator("option")).toHaveText([
      "Organization default",
      "Off (fastest)",
      "Minimal",
      "Low",
      "Medium",
      "High",
      "Extra high",
      "Max",
    ]);
    await effort.selectOption("none");
    await page.getByTestId("agent-save").click();
    await expect(page.getByTestId("agent-save")).toHaveText(/Saved/);

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === "Quick Decider");
    expect(saved.reasoning_effort).toBe("none");
  });
});
