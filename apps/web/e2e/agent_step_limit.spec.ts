import { test, expect } from "@playwright/test";

/**
 * A custom agent can set its own step limit, up to the organization's `max_turns`
 * (60 unless the deployment changes it); empty inherits the organization's.
 */
test.describe("Agent step limit", () => {
  test("is bounded by the organization's limit and persists once valid", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByTestId("agent-new").click();
    await page.getByTestId("agent-name").fill("Bounded Researcher");
    await page.getByTestId("agent-section-limits").click();

    const limits = await (await page.request.get("/v1/agents/limits")).json();
    const ceiling: number = limits.max_turns;
    const field = page.getByLabel("Max steps per turn");
    await expect(field).toHaveAttribute("max", String(ceiling));
    await expect(page.getByText(`organization's limit (${ceiling})`)).toBeVisible();

    await field.fill(String(ceiling + 1));
    await page.getByTestId("agent-save").click();
    await expect(page.getByTestId("persona-save-error")).toContainText(`limit of ${ceiling}`);

    await field.fill("12");
    await page.getByTestId("agent-save").click();
    await expect(page.getByTestId("agent-save")).toHaveText(/Saved/);

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === "Bounded Researcher");
    expect(saved.max_turns).toBe(12);
  });
});
