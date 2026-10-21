import { test, expect } from "@playwright/test";

/**
 * An agent chooses which of its tools every step carries. "On demand" keeps the platform's tools
 * loaded and lists the MCP tools that are not pinned by name; "Only pinned tools" loads nothing
 * but what is pinned. Either way a deferred tool is loaded when the agent needs it.
 */
test.describe("Agent tool loading", () => {
  test("a mode, pinned tools and patterns are saved with the agent", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("Pinned Investigator");

    await page.getByTestId("agent-tool-loading").selectOption("pinned");
    const pin = page.getByTestId("agent-pin-file_read");
    await pin.click();
    await expect(pin).toHaveAttribute("aria-pressed", "true");
    const always = page.getByTestId("agent-active-tools");
    await expect(always).toHaveValue("file_read");
    await always.fill("file_read\nmcp__signoz*__*search*");

    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByText("SAVED!")).toBeVisible();

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === "Pinned Investigator");
    expect(saved.tool_loading).toBe("pinned");
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

    // On demand keeps the platform's tools loaded, so pinning one would do nothing and is not offered.
    await page.getByTestId("agent-tool-loading").selectOption("on_demand");
    await expect(summary).toContainText("The platform's own tools");
    await expect(page.getByTestId("tool-state-active").first()).toBeVisible();
    await expect(page.getByTestId("agent-pin-file_read")).toHaveCount(0);

    // With only pinned tools loaded, everything else the agent may use is deferred.
    await page.getByTestId("agent-tool-loading").selectOption("pinned");
    await expect(summary).toContainText("No tool is pinned");
    await page.getByTestId("agent-pin-file_read").click();
    await expect(summary).toContainText("Only the 1 pinned tool (and the loading tools)");
    await expect(page.getByTestId("tool-count-active")).toHaveText("1");
    await expect(page.getByTestId("tool-count-deferred")).not.toHaveText("0");
    await expect(page.getByTestId("tool-state-active").first()).toBeVisible();
    await expect(page.getByTestId("tool-state-deferred").first()).toBeVisible();

    // Back to the organization's mode: the summary says whose choice it is.
    await page.getByTestId("agent-tool-loading").selectOption("");
    await expect(summary).toContainText("Organization default");
  });
});

