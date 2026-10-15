import { test, expect } from "@playwright/test";

/**
 * An agent chooses which of its tools every step carries: pinned tools and patterns are always
 * loaded; with "On demand" the others are listed by name and loaded when the agent needs them.
 */
test.describe("Agent tool loading", () => {
  test("a mode, pinned tools and patterns are saved with the agent", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("On-demand Investigator");

    await page.getByTestId("agent-tool-loading").selectOption("on_demand");
    const pin = page.getByTestId("agent-pin-file_read");
    await pin.click();
    await expect(pin).toHaveAttribute("aria-pressed", "true");
    const always = page.getByTestId("agent-active-tools");
    await expect(always).toHaveValue("file_read");
    await always.fill("file_read\nmcp__signoz*__*search*");

    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByText("SAVED!")).toBeVisible();

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === "On-demand Investigator");
    expect(saved.tool_loading).toBe("on_demand");
    expect(saved.active_tools).toEqual(["file_read", "mcp__signoz*__*search*"]);
  });
});
