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

  test("the page shows which tools are active and which are deferred, and why", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("Loading Preview Agent");
    const summary = page.getByTestId("tool-load-summary");

    await page.getByTestId("agent-tool-loading").selectOption("all");
    await expect(summary).toContainText("Every tool loaded");
    await expect(summary).toContainText("Set on this agent");
    await expect(page.getByTestId("tool-count-deferred")).toHaveText("0");
    await expect(page.getByTestId("tool-state-deferred")).toHaveCount(0);

    // With only pinned tools loaded, everything else the agent may use is deferred.
    await page.getByTestId("agent-tool-loading").selectOption("on_demand");
    await page.getByTestId("agent-pin-file_read").click();
    await expect(summary).toContainText("Only the pinned tools are sent with every step");
    await expect(page.getByTestId("tool-count-active")).toHaveText(/^[1-9]\d*$/);
    await expect(page.getByTestId("tool-count-deferred")).not.toHaveText("0");
    await expect(page.getByTestId("tool-state-active").first()).toBeVisible();
    await expect(page.getByTestId("tool-state-deferred").first()).toBeVisible();

    // Back to the organization's mode: the summary says whose choice it is.
    await page.getByTestId("agent-tool-loading").selectOption("");
    await expect(summary).toContainText("Organization default");
  });
});

