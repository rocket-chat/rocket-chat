import { test, expect } from "@playwright/test";

test.describe("Agent Cockpit E2E Verification", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ page }) => {
    // Navigate to local mission control web app
    await page.goto("/");
  });

  test("renders telemetry header and avionics branding", async ({ page }) => {
    await expect(page).toHaveURL(/\/chat\/.+/, { timeout: 15000 });
    await expect(page.locator("aside, header, main").getByText("Rocket Chat").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Ready|Executing/i).first()).toBeVisible({ timeout: 10000 });
  });

  test("allows selecting model or agent from dynamic dropdown", async ({ page }) => {
    // Mode selector button
    const modeSelector = page.getByTitle("Select Active Language Model or Agent");
    await expect(modeSelector).toBeVisible();
    await expect(modeSelector).toBeEnabled({ timeout: 10000 });

    // Open dropdown
    await modeSelector.click();
    await expect(page.getByText("EXECUTION PERSONA OR MODEL")).toBeVisible();

    // Verify specialized agent option is visible
    const auditorOption = page.locator("button").filter({ hasText: /Security & Penetration Auditor/i }).last();
    await expect(auditorOption).toBeVisible();

    // Use live search filter to isolate model among hundreds of live provider models
    const searchFilter = page.getByPlaceholder(/Filter models/i);
    await searchFilter.fill("Sonnet");
    await expect(page.getByText(/Claude Sonnet/i).first()).toBeVisible();

    // Select Claude Sonnet as direct model
    await page.locator("button").filter({ hasText: /Claude Sonnet/i }).first().click();

    // Dropdown closes and mode selector updates to direct model
    await expect(page.getByText("EXECUTION PERSONA OR MODEL")).not.toBeVisible();
    await expect(modeSelector).toContainText("Claude Sonnet");

    // Re-open and select specialized agent persona
    await modeSelector.click();
    await expect(page.getByText("EXECUTION PERSONA OR MODEL")).toBeVisible();
    await page.locator("button").filter({ hasText: /Security & Penetration Auditor/i }).last().click();
    await expect(modeSelector).toContainText("Security & Penetration Auditor");
  });

  test("toggles collapsible right panel (collapsed by default)", async ({ page }) => {
    // Right panel is collapsed by default to declutter the workspace
    const expandButton = page.getByTitle(/Expand Right Panel|Show Panel/i);
    await expect(expandButton).toBeVisible();

    // Click to expand
    await expandButton.click();

    // Verify right panel is visible and toggle button changes to Hide Panel
    const collapseButton = page.getByTitle(/Collapse Right Panel|Hide Panel/i);
    await expect(collapseButton).toBeVisible();

    // Click to collapse again
    await collapseButton.click();
    await expect(page.getByTitle(/Expand Right Panel|Show Panel/i)).toBeVisible();
  });

  test("opens and navigates enterprise settings suite", async ({ page }) => {
    const settingsButton = page.getByTitle(/Settings Console/i);
    await settingsButton.click();

    // Verify full-page settings suite is opened
    await expect(page).toHaveURL(/\/settings/);

    // Navigate to Agents tab
    await page.getByRole("link", { name: /Custom Agents/i }).click();
    await expect(page).toHaveURL(/\/settings\/agents/);
    await expect(page.getByText("CUSTOM AGENT PERSONAS & CONTEXT")).toBeVisible();
    await expect(page.getByText("General Software Engineer").first()).toBeVisible();

    // Navigate to MCP tab
    await page.getByRole("link", { name: /MCP Servers/i }).click();
    await expect(page).toHaveURL(/\/settings\/mcp/);
    await expect(page.getByRole("heading", { name: /MODEL CONTEXT PROTOCOL/i })).toBeVisible();

    // Return to Cockpit
    await page.getByRole("link", { name: /Back to Cockpit|Cockpit/i }).first().click();
    await expect(page).toHaveURL(/(\/|\/chat\/.*)/);
  });

  test("submits user instruction and does not hang indefinitely", async ({ page }) => {
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeEnabled();
    await input.fill("hey");

    const runButton = page.getByTestId("ignite-button");
    await runButton.click();

    // Verify message appears in stream immediately
    await expect(page.getByText("hey", { exact: true }).first()).toBeVisible();

    // Ensure system is alive and processing
    await expect(page.getByText(/Executing|Synthesizing|Ready/i).first()).toBeVisible({ timeout: 5000 });
  });
});
