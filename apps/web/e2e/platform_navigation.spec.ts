import { test, expect } from "@playwright/test";

test.describe("Full-Fledged Platform Navigation & Markdown Engine", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("renders collapsible session sidebar with agent selector and sessions list", async ({ page }) => {
    const sidebar = page.getByTestId("session-sidebar");
    await expect(sidebar).toBeVisible();

    // Verify Brand Logo in sidebar
    await expect(sidebar.getByText("Rocket Chat")).toBeVisible();

    // Verify Active Agent Selector displays assigned agent
    await expect(page.getByText("SPECIALIZED AGENT")).toBeVisible();
    const agentDropdownBtn = page.getByTestId("assigned-agent-button");
    await expect(agentDropdownBtn).toBeVisible();

    // Open agent selector dropdown and choose an agent
    await agentDropdownBtn.click();
    await expect(page.getByText("Security & Penetration Auditor").first()).toBeVisible();

    await page.getByRole("button", { name: /Security & Penetration Auditor/i }).first().click();

    // Verify active agent changed
    await expect(agentDropdownBtn).toContainText("Security & Penetration Auditor");
  });

  test("creates new mission session in sidebar list", async ({ page }) => {
    const newMissionBtn = page.getByTestId("new-conversation-btn");
    await expect(newMissionBtn).toBeVisible();

    // Count current sessions
    const sessionsLabel = page.getByText(/Sessions/i).first();
    await expect(sessionsLabel).toBeVisible();

    // Click to create new mission
    await newMissionBtn.click();
    await expect(page).toHaveURL(/\/chat\/.+/);

    // Verify session stream reset
    await expect(page.getByText(/Ready|Executing|New Session|Mission Control/i).first()).toBeVisible();
  });

  test("toggles collapsible left sidebar", async ({ page }) => {
    const sidebar = page.getByTestId("session-sidebar");
    await expect(sidebar).toBeVisible();

    // Header toggle button
    const toggleBtn = page.getByTitle(/Collapse Sidebar|Expand Sessions Sidebar/i);
    await expect(toggleBtn).toBeVisible();

    // Collapse sidebar
    await toggleBtn.click();
    await expect(sidebar).not.toBeVisible();

    // Expand sidebar again
    const expandBtn = page.getByTitle(/Expand Sessions Sidebar/i);
    await expandBtn.click();
    await expect(sidebar).toBeVisible();
  });

  test("navigates through full-page settings suite", async ({ page }) => {
    // Navigate to settings via sidebar settings button
    const settingsLink = page.getByTestId("sidebar-settings-link");
    await settingsLink.click();

    // Verify navigated to settings suite
    await expect(page).toHaveURL(/\/settings/);
    await expect(page.getByText("Settings Console").first()).toBeVisible();

    // Navigate to Custom Agents page
    await page.getByRole("link", { name: /Custom Agents/i }).click();
    await expect(page).toHaveURL(/\/settings\/agents/);
    await expect(page.getByText("Custom agent personas")).toBeVisible();
    await expect(page.getByText(/EDIT:/i).first()).toBeVisible();

    // Test Markdown Preview toggle for system prompt
    const previewBtn = page.getByRole("button", { name: /Preview Markdown/i });
    await previewBtn.click();
    await expect(page.getByTestId("markdown-preview-box")).toBeVisible();

    // Navigate to MCP Servers page
    await page.getByRole("link", { name: /MCP Servers/i }).click();
    await expect(page).toHaveURL(/\/settings\/mcp/);
    await expect(page.getByRole("heading", { level: 1, name: /MCP servers/i })).toBeVisible();

    // Navigate to Custom Skills page
    await page.getByRole("link", { name: /Skills & Tools/i }).click();
    await expect(page).toHaveURL(/\/settings\/skills/);
    await expect(page.getByRole("heading", { name: /Skills & tools/i })).toBeVisible();

    // Navigate to Knowledge Bases page
    await page.getByRole("link", { name: /Knowledge Bases/i }).click();
    await expect(page).toHaveURL(/\/settings\/knowledge/);
    await expect(page.getByRole("heading", { name: /Knowledge bases/i })).toBeVisible();

    // Return to Cockpit
    await page.getByRole("link", { name: /Back to Cockpit|Cockpit/i }).first().click();
    await expect(page).toHaveURL(/(\/|\/chat\/.*)/);
    await expect(page.getByTestId("session-sidebar")).toBeVisible();
  });

  test("configures agent tool permissions including Allow All option", async ({ page }) => {
    // Navigate to settings -> agents
    await page.goto("/settings/agents");
    await expect(page.getByText("Custom agent personas")).toBeVisible();

    // Verify Allow All switch is present
    const allowAllBtn = page.getByRole("button", { name: /ALLOW ALL TOOLS/i });
    await expect(allowAllBtn).toBeVisible();

    // The tool list is driven by the live backend catalog, grouped by category.
    await expect(page.getByText("AUTHORIZED TOOLS & MCP SERVERS")).toBeVisible();
    await expect(page.getByText("Sandbox & Repository", { exact: true })).toBeVisible();
    await expect(page.getByTestId("agent-tool-bash_exec")).toBeVisible();

    // Click Allow All
    await allowAllBtn.click();
    await expect(page.getByText(/Full Access Granted:/i)).toBeVisible();

    // Save configuration and verify backend response 200
    const savePromise = page.waitForResponse(
      (res) => res.url().includes("/v1/agents") && res.request().method() === "POST"
    );
    const saveBtn = page.getByRole("button", { name: /SAVE PERSONA/i });
    await saveBtn.click();
    const saveRes = await savePromise;
    expect(saveRes.status()).toBe(200);

    await expect(page.getByTestId("persona-save-error")).not.toBeVisible();
    await expect(page.getByText("SAVED!")).toBeVisible();
  });

  test("creates, configures, and saves a brand new custom agent persona with 200 OK persistence", async ({ page }) => {
    await page.goto("/settings/agents");
    await expect(page.getByText("Custom agent personas")).toBeVisible();

    // Click New Agent Persona
    const newAgentBtn = page.getByRole("button", { name: /New agent/i });
    await newAgentBtn.click();

    // Form should have default name input
    const nameInput = page.locator("input").first();
    await nameInput.fill("Autonomous Chaos Engineer");

    // Click Save Persona and verify network response is 200
    const savePromise = page.waitForResponse(
      (res) => res.url().includes("/v1/agents") && res.request().method() === "POST"
    );
    const saveBtn = page.getByRole("button", { name: /SAVE PERSONA/i });
    await saveBtn.click();
    const saveRes = await savePromise;
    expect(saveRes.status()).toBe(200);

    // Verify no error banner and saved indicator
    await expect(page.getByTestId("persona-save-error")).not.toBeVisible();
    await expect(page.getByText("SAVED!")).toBeVisible();

    // Verify new persona appears in available catalog list
    await expect(page.getByText("Autonomous Chaos Engineer").first()).toBeVisible();

    // Return to Cockpit and verify the agent is available in assigned agent picker
    await page.goto("/");
    const agentDropdownBtn = page.getByTestId("assigned-agent-button");
    await agentDropdownBtn.click();
    await expect(page.getByText("Autonomous Chaos Engineer").first()).toBeVisible();
  });

  test("renders rich markdown and code blocks in flight log stream", async ({ page }) => {
    // Send markdown formatted message
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeEnabled();
    const markdownPrompt = "### Analysis Report\nHere is a code snippet:\n```python\ndef test_fn():\n    return True\n```\n- Check complete";
    await input.fill(markdownPrompt);

    const runBtn = page.getByTestId("ignite-button");
    await runBtn.click();

    // Verify markdown heading, list, and code block rendered with copy button
    await expect(page.locator("h3:has-text('Analysis Report')").first()).toBeVisible();
    await expect(page.getByText("Check complete").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /Copy/i }).first()).toBeVisible();
  });

  test("toggles between light, dark, and system themes seamlessly", async ({ page }) => {
    await page.goto("/");
    const themeBtn = page.getByRole("button", { name: /Toggle theme/i }).first();
    await expect(themeBtn).toBeVisible();

    // Click to cycle theme
    await themeBtn.click();
    const mode1 = await page.evaluate(() => localStorage.getItem("theme_mode"));
    expect(["light", "dark", "system"]).toContain(mode1);

    // Click again to cycle to next theme
    await themeBtn.click();
    const mode2 = await page.evaluate(() => localStorage.getItem("theme_mode"));
    expect(["light", "dark", "system"]).toContain(mode2);
    expect(mode2).not.toEqual(mode1);
  });
});

