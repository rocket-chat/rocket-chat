import { test, expect } from "@playwright/test";

/**
 * A custom agent can set its own step limit, up to the organization's `max_turns`
 * (60 unless the deployment changes it); empty inherits the organization's.
 */
test.describe("Agent step limit", () => {
  test("is bounded by the organization's limit and persists once valid", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("Bounded Researcher");

    const limits = await (await page.request.get("/v1/agents/limits")).json();
    const ceiling: number = limits.max_turns;
    const field = page.getByLabel("Max steps per turn");
    await expect(field).toHaveAttribute("max", String(ceiling));
    await expect(page.getByText(`at most ${ceiling}`)).toBeVisible();

    await field.fill(String(ceiling + 1));
    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByTestId("persona-save-error")).toContainText(`limit of ${ceiling}`);

    await field.fill("12");
    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByText("SAVED!")).toBeVisible();

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === "Bounded Researcher");
    expect(saved.max_turns).toBe(12);
  });
});
