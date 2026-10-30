import { test, expect } from "@playwright/test";

test.describe("Full-Fledged Platform Navigation & Markdown Engine", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("the sidebar lists sessions and the composer chooses the agent", async ({ page }) => {
    const sidebar = page.getByTestId("session-sidebar");
    await expect(sidebar).toBeVisible();
    await expect(sidebar.getByText("Rocket Chat")).toBeVisible();
    // One place chooses the agent: the composer, for the next message.
    await expect(page.getByTestId("assigned-agent-button")).toHaveCount(0);

    const picker = page.getByTestId("agent-model-picker");
    await picker.click();
    await page.getByRole("button", { name: /Security Auditor/i }).first().click();

    await expect(picker).toContainText("Security Auditor");
  });

  test("creates new mission session in sidebar list", async ({ page }) => {
    const newMissionBtn = page.getByTestId("new-conversation-btn");
    await expect(newMissionBtn).toBeVisible();

    // The conversation list is searchable
    await expect(page.getByTestId("session-search")).toBeVisible();

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
    await page.getByRole("navigation", { name: "Organization" }).getByRole("link", { name: /^Agents/ }).click();
    await expect(page).toHaveURL(/\/settings\/agents/);
    await expect(page.getByRole("heading", { level: 1, name: "Agents" })).toBeVisible();
    await page.getByTestId("agent-item-persona-general").click();
    await expect(page.getByTestId("agent-editor-title")).toBeVisible();

    // Test Markdown Preview toggle for system prompt
    const previewBtn = page.getByRole("button", { name: "Preview", exact: true });
    await previewBtn.click();
    await expect(page.getByTestId("markdown-preview-box")).toBeVisible();

    // Navigate to MCP Servers page
    await page.getByRole("link", { name: "MCP servers", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/mcp/);
    await expect(page.getByRole("heading", { level: 1, name: /MCP servers/i })).toBeVisible();

    // Navigate to Custom Skills page
    await page.getByRole("link", { name: "Skills", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/skills/);
    await expect(page.getByRole("heading", { name: /Skills & tools/i })).toBeVisible();

    // Navigate to Knowledge Bases page
    await page.getByRole("link", { name: "Knowledge bases", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/knowledge/);
    await expect(page.getByRole("heading", { name: /Knowledge bases/i })).toBeVisible();

    // Return to Cockpit
    await page.getByRole("link", { name: /Back to Cockpit|Cockpit/i }).first().click();
    await expect(page).toHaveURL(/(\/|\/chat\/.*)/);
    await expect(page.getByTestId("session-sidebar")).toBeVisible();
  });

  test("configures agent tool permissions including Allow All option", async ({ page }) => {
    await page.goto("/settings/agents");
    await expect(page.getByRole("heading", { level: 1, name: "Agents" })).toBeVisible();

    await page.getByTestId("agent-new").click();
    await page.getByTestId("agent-name").fill("Narrowed Tool User");
    await page.getByTestId("agent-section-tools").click();

    // A new agent may use every tool, including ones added later.
    const allowAll = page.getByTestId("agent-allow-all");
    await expect(allowAll).toBeChecked();

    // The tool list is driven by the live backend catalog, grouped by the approval it needs.
    await expect(page.getByRole("region", { name: "Commands tools" })).toBeVisible();
    await expect(page.getByTestId("agent-tool-bash_exec")).toBeVisible();

    // Narrow it, then allow everything again.
    await allowAll.uncheck();
    await expect(page.getByTestId("agent-tool-bash_exec").getByRole("checkbox")).not.toBeChecked();
    await allowAll.check();
    await expect(page.getByTestId("agent-tool-bash_exec").getByRole("checkbox")).toBeChecked();

    const savePromise = page.waitForResponse(
      (res) => res.url().includes("/v1/agents") && res.request().method() === "POST"
    );
    await page.getByTestId("agent-save").click();
    const saveRes = await savePromise;
    expect(saveRes.status()).toBe(200);
    expect((await saveRes.json()).whitelisted_tools).toEqual(["*"]);

    await expect(page.getByTestId("persona-save-error")).not.toBeVisible();
    await expect(page.getByTestId("agent-save")).toHaveText(/Saved/);
  });

  test("creates, configures, and saves a brand new custom agent persona with 200 OK persistence", async ({ page }) => {
    await page.goto("/settings/agents");
    await expect(page.getByRole("heading", { level: 1, name: "Agents" })).toBeVisible();

    await page.getByTestId("agent-new").click();
    await page.getByTestId("agent-name").fill("Autonomous Chaos Engineer");

    const savePromise = page.waitForResponse(
      (res) => res.url().includes("/v1/agents") && res.request().method() === "POST"
    );
    await page.getByTestId("agent-save").click();
    const saveRes = await savePromise;
    expect(saveRes.status()).toBe(200);

    await expect(page.getByTestId("persona-save-error")).not.toBeVisible();
    await expect(page.getByTestId("agent-save")).toHaveText(/Saved/);

    // Verify new persona appears in the list
    await expect(page.getByRole("list", { name: "Agents" }).getByText("Autonomous Chaos Engineer")).toBeVisible();

    // Return to Cockpit and verify the agent is available in the composer's picker
    await page.goto("/");
    await page.getByTestId("agent-model-picker").click();
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
    // On desktop the theme control lives in the View menu.
    await page.getByTestId("view-menu-trigger").click();
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

