import { test, expect } from "@playwright/test";

/** The agent editor says when a prompt names an argument a tool does not take, or grows too long. */
test.describe("Agent prompt warnings", () => {
  test("a wrong tool argument and an oversized prompt are flagged, and saving still works", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("Linted Agent");
    const prompt = page.getByPlaceholder("Enter markdown-formatted system instructions...");

    await prompt.fill("Wait for CI with `wait_for(ci_status, pr=<url>)`.");
    await expect(page.getByTestId("persona-warning-unknown_argument")).toContainText("has no `pr` parameter");

    await prompt.fill("Be precise. ".repeat(400));
    await expect(page.getByTestId("persona-warning-too_long")).toBeVisible();
    await expect(page.getByTestId("persona-warning-unknown_argument")).toHaveCount(0);

    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByText("SAVED!")).toBeVisible();
  });
});
