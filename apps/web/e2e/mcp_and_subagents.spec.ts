import { test, expect } from "@playwright/test";

test.describe("MCP Servers & Subagents Settings E2E Verification", () => {
  test.describe.configure({ mode: "serial" });

  test("the registration form is a dialog that Escape closes", async ({ page }) => {
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /REGISTER MCP SERVER/i }).click();
    const dialog = page.getByTestId("mcp-server-modal");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("aria-modal", "true");

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });

  test("a common server prefills the form with a setup guide, and the old connector panel is gone", async ({ page }) => {
    await page.goto("/settings/mcp");
    await expect(page.getByText("Connector catalog")).toHaveCount(0);

    await page.getByRole("button", { name: /REGISTER MCP SERVER/i }).click();
    await page.getByTestId("mcp-preset").selectOption("linear");

    await expect(page.getByPlaceholder("e.g. Stripe Billing MCP")).toHaveValue("Linear");
    await expect(page.getByPlaceholder("https://api.internal.service/mcp")).toHaveValue("https://mcp.linear.app/mcp");
    const note = page.getByTestId("mcp-preset-note");
    await expect(note).toContainText("/v1/mcp/oauth/callback");
    await expect(note.getByRole("link", { name: /Setup guide/ })).toHaveAttribute("href", "https://linear.app/docs/mcp");
    await expect(page.getByLabel("Authorization URL")).toHaveValue("https://linear.app/oauth/authorize");
  });

  test("a command server is configured with environment variables", async ({ page }) => {
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /Add org MCP/i }).click();
    await page.getByTestId("mcp-preset").selectOption("brave-search");

    await expect(page.getByLabel("Environment", { exact: true })).toHaveValue("custom_headers");
    await expect(page.getByPlaceholder(/Variable name/)).toHaveValue("BRAVE_API_KEY");
    await expect(page.getByText("Authentication Mode:")).toHaveCount(0);
    await expect(page.getByRole("option", { name: /OAuth/ })).toHaveCount(0);
  });

  test("manages multi-scheme MCP servers with write-only credentials and prompt guidance", async ({ page }) => {
    await page.goto("/settings/mcp");

    // Header & description verification
    await expect(page.getByRole("heading", { name: /MODEL CONTEXT PROTOCOL/i })).toBeVisible();
    await expect(page.getByText(/Connect HTTP, SSE, and STDIO tool providers/i)).toBeVisible();

    // Only servers that really exist are listed - no pre-seeded "connected" built-ins.
    const mainContent = page.getByRole("main");
    await expect(mainContent.getByText(/Workspace Filesystem/i)).toHaveCount(0);
    await expect(mainContent.getByText("GitHub Integration")).toHaveCount(0);

    // Click to expand "REGISTER MCP SERVER" form
    const registerBtn = page.getByRole("button", { name: /REGISTER MCP SERVER/i });
    await expect(registerBtn).toBeVisible();
    await registerBtn.click();

    // The form is a dialog over the page, not a block somewhere in the scrolled content.
    await expect(page.getByTestId("mcp-server-modal")).toBeVisible();

    // Fill server details
    await page.getByPlaceholder("e.g. Stripe Billing MCP").fill("E2E Sentry Server");

    // Verify transport options (stdio, http, sse)
    const transportSelect = page.getByLabel("Transport", { exact: true });
    await expect(transportSelect).toBeVisible();
    await transportSelect.selectOption("http");

    // Fill HTTP endpoint
    await page.getByPlaceholder("https://api.internal.service/mcp").fill("https://sentry.corp.internal/mcp");

    // Guidance is optional, so it sits behind a disclosure
    await page.getByTestId("mcp-guidance-toggle").click();
    const guidanceInput = page.getByPlaceholder(/Always check the customer currency/i);
    await guidanceInput.fill("Use this server to fetch exception tracebacks.");

    // Select Custom Headers auth mode
    const authSelect = page.getByLabel("Authentication Mode");
    await authSelect.selectOption("custom_headers");

    // Add custom header with secret toggle
    await page.getByPlaceholder("Header Key (e.g. X-Org-ID)").fill("X-Security-Token");
    await page.getByPlaceholder("Header Value").fill("super-secret-custom-token");

    // Check "Secret" toggle
    const secretCheckbox = page.locator("input[type='checkbox']").last();
    await secretCheckbox.check();

    // Click Add Header
    await page.getByRole("button", { name: /Add Header/i }).click();

    // Verify masked preview is shown
    await expect(page.getByText(/X-Security-Token: ••••••••/i)).toBeVisible();

    // Submit registration
    const submitBtn = page.getByRole("button", { name: /PROBE & CONNECT SERVER/i });
    await expect(submitBtn).toBeVisible();
    await submitBtn.click();

    // Verify new server appears in list
    await expect(mainContent.getByText("E2E Sentry Server", { exact: true })).toBeVisible();
    await expect(mainContent.getByText("http").first()).toBeVisible();

    // Verify cleartext secret is NEVER shown in DOM
    const pageContent = await page.content();
    expect(pageContent).not.toContain("super-secret-custom-token");
  });

  test("configures autonomous specialist subagents and saves behavioral settings", async ({ page }) => {
    await page.goto("/settings/subagents");

    const mainContent = page.getByRole("main");

    // Header verification
    await expect(mainContent.getByRole("heading", { name: /Subagents & Task Delegation/i })).toBeVisible();
    await expect(mainContent.getByText(/Configure autonomous specialists that execute focused/i)).toBeVisible();

    // Verify specialist list
    await expect(mainContent.getByRole("button", { name: /Security/i })).toBeVisible();
    await expect(mainContent.getByRole("button", { name: /QA/i })).toBeVisible();
    await expect(mainContent.getByRole("button", { name: /Researcher/i })).toBeVisible();

    // Verify recursion safeguard notice
    await expect(mainContent.getByText("Recursion Safeguard")).toBeVisible();
    await expect(mainContent.getByText(/Subagents inherit the session workspace but are strictly prohibited/i)).toBeVisible();

    // Ensure list is hydrated and select QA Verifier
    const qaButton = page.getByTestId("subagent-item-qa_verifier");
    await expect(qaButton).toBeVisible({ timeout: 10000 });
    await qaButton.click();
    await expect(page.getByTestId("selected-subagent-title")).toHaveText(/QA/i, { timeout: 10000 });

    // Verify form inputs populated
    const roleInput = mainContent.locator("input[type='text']").first();
    await expect(roleInput).toBeVisible();

    // Verify turn limit range slider
    const turnSlider = mainContent.locator("input[type='range']").first();
    await expect(turnSlider).toBeVisible();
    await turnSlider.fill("6");
    await expect(mainContent.getByText("6 turns", { exact: true })).toBeVisible();

    // Toggle a tool permission
    const bashToolCheck = mainContent.locator("label").filter({ hasText: "Bash Terminal Exec" });
    await expect(bashToolCheck).toBeVisible();

    // Click Save Configuration
    const saveBtn = mainContent.getByRole("button", { name: /Save Configuration/i });
    await expect(saveBtn).toBeVisible();
    await saveBtn.click();

    // Verify success confirmation badge
    await expect(mainContent.getByText(/Saved Settings/i)).toBeVisible();
  });

  test("loads dynamic model list in user preferences settings", async ({ page }) => {
    await page.goto("/settings/user/models");

    const mainContent = page.getByRole("main");
    await expect(mainContent.getByRole("heading", { name: /Model Defaults/i })).toBeVisible();

    // Verify select element is present and contains populated models from API
    const select = mainContent.locator("select");
    await expect(select).toBeVisible();

    // Expect multiple options/optgroups loaded dynamically
    const options = select.locator("option");
    await expect(options).not.toHaveCount(0);

    // Save changes
    const saveButton = page.getByRole("button", { name: /Save Changes/i });
    await expect(saveButton).toBeVisible();
    await saveButton.click();

    await expect(page.getByText(/Model and latency settings saved/i)).toBeVisible();
  });
});
