import { test, expect } from "@playwright/test";

/**
 * Every agent's steps carry the core tools; an agent keeps more active by pinning them (or by a
 * pattern), and every other tool it may use is listed by name and loaded when it needs it. Past
 * the limit of tools sent with every step, the page warns.
 */
test.describe("Agent tool loading", () => {
  test("core tools need no pin, and kept-active tools and patterns are saved", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("Pinned Investigator");
    await page.getByText("Allow all tools").click();

    await expect(page.getByTestId("tool-state-core").first()).toBeVisible();
    await expect(page.getByTestId("agent-pin-file_read")).toHaveCount(0);
    const pin = page.getByTestId("agent-pin-git_status");
    await pin.click();
    await expect(pin).toHaveAttribute("aria-pressed", "true");
    const kept = page.getByTestId("agent-active-tools");
    await expect(kept).toHaveValue("git_status");
    await kept.fill("git_status\nmcp__signoz*__*search*");

    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByText("SAVED!")).toBeVisible();

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === "Pinned Investigator");
    expect(saved.active_tools).toEqual(["git_status", "mcp__signoz*__*search*"]);
    expect(saved.tool_loading).toBeUndefined();
  });

  test("the page counts what every step carries and warns past the limit", async ({ page }) => {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill("Loading Preview Agent");
    await page.getByText("Allow all tools").click();
    const summary = page.getByTestId("tool-load-summary");

    await expect(summary).toContainText("Core tools and the ones kept active");
    await expect(page.getByTestId("tool-count-deferred")).not.toHaveText("0");
    await expect(page.getByTestId("tool-state-deferred").first()).toBeVisible();
    await expect(page.getByTestId("tool-load-warning")).toHaveCount(0);
    const before = Number(await page.getByTestId("tool-count-active").textContent());

    await page.getByTestId("agent-pin-git_status").click();
    await expect(page.getByTestId("tool-count-active")).toHaveText(String(before + 1));

    // Keeping everything active sends every tool with every step: the page says so.
    await page.getByTestId("agent-active-tools").fill("*");
    await expect(page.getByTestId("tool-count-deferred")).toHaveText("0");
    await expect(page.getByTestId("tool-load-warning")).toBeVisible();
  });
});
