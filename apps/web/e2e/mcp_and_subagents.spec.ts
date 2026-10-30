import { test, expect } from "@playwright/test";

test.describe("MCP Servers & Subagents Settings E2E Verification", () => {
  test.describe.configure({ mode: "serial" });

  test("the registration form is a dialog that Escape closes", async ({ page }) => {
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /Add server/i }).click();
    const dialog = page.getByTestId("mcp-server-modal");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("aria-modal", "true");

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });

  test("a common server prefills the form with a setup guide, and the old connector panel is gone", async ({ page }) => {
    await page.goto("/settings/mcp");
    await expect(page.getByText("Connector catalog")).toHaveCount(0);

    await page.getByRole("button", { name: /Add server/i }).click();
    await page.getByTestId("mcp-preset").selectOption("linear");
    const note = page.getByTestId("mcp-preset-note");
    await expect(note).toContainText("registers itself");
    await expect(note).toContainText("/v1/mcp/oauth/callback");
    await expect(note.getByRole("link", { name: /Setup guide/ })).toHaveAttribute("href", "https://linear.app/docs/mcp");

    await page.getByTestId("mcp-next").click();
    await expect(page.getByTestId("mcp-step-connection")).toHaveAttribute("aria-current", "step");
    await expect(page.getByPlaceholder("e.g. Stripe Billing MCP")).toHaveValue("Linear");
    await expect(page.getByPlaceholder("https://api.internal.service/mcp")).toHaveValue("https://mcp.linear.app/mcp");
    // Nothing to fill in: the endpoints and client are discovered when signing in.
    await page.getByTestId("mcp-next").click();
    await expect(page.getByTestId("mcp-sign-in-summary")).toContainText("Nothing to set up");
  });

  test("a command server is configured with environment variables", async ({ page }) => {
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /Add org server/i }).click();
    await page.getByTestId("mcp-preset").selectOption("brave-search");
    await page.getByTestId("mcp-step-auth").click();

    await expect(page.getByLabel("Variable 1 name")).toHaveValue("BRAVE_API_KEY");
    await expect(page.getByTestId("env-row").first().getByRole("checkbox").nth(1)).toBeChecked();
    await expect(page.getByLabel("Authentication Mode")).toHaveCount(0);
    await expect(page.getByRole("option", { name: /OAuth/ })).toHaveCount(0);
  });

  test("manages multi-scheme MCP servers with write-only credentials and prompt guidance", async ({ page }) => {
    await page.goto("/settings/mcp");

    // Header & description verification
    await expect(page.getByRole("heading", { level: 1, name: /MCP servers/i })).toBeVisible();
    await expect(page.getByText(/Connect tool providers over HTTP, SSE or as a command/i)).toBeVisible();

    // Only servers that really exist are listed - no pre-seeded "connected" built-ins.
    const mainContent = page.getByRole("main");
    await expect(mainContent.getByText(/Workspace Filesystem/i)).toHaveCount(0);
    await expect(mainContent.getByText("GitHub Integration")).toHaveCount(0);

    // Click to expand "REGISTER MCP SERVER" form
    const registerBtn = page.getByRole("button", { name: /Add server/i });
    await expect(registerBtn).toBeVisible();
    await registerBtn.click();

    // The form is a dialog over the page, not a block somewhere in the scrolled content.
    await expect(page.getByTestId("mcp-server-modal")).toBeVisible();

    // The wizard opens on its presets; a custom server starts from the connection.
    await expect(page.getByTestId("mcp-step-preset")).toHaveAttribute("aria-current", "step");
    await page.getByTestId("mcp-next").click();

    // Fill server details
    await page.getByPlaceholder("e.g. Stripe Billing MCP").fill("E2E Sentry Server");

    // Verify transport options (stdio, http, sse)
    const transportSelect = page.getByLabel("Transport", { exact: true });
    await expect(transportSelect).toBeVisible();
    await transportSelect.selectOption("http");

    // Fill HTTP endpoint
    await page.getByPlaceholder("https://api.internal.service/mcp").fill("https://sentry.corp.internal/mcp");

    // Select Custom Headers auth mode
    await page.getByTestId("mcp-next").click();
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

    // Guidance is optional, so it sits behind a disclosure on the last step
    await page.getByTestId("mcp-next").click();
    await page.getByTestId("mcp-guidance-toggle").click();
    const guidanceInput = page.getByTestId("guidance-input");
    await guidanceInput.fill("Use this server to fetch exception tracebacks.");

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

  test("subagent roles are edited with the agents, and the old page leads there", async ({ page }) => {
    await page.goto("/settings/subagents");
    await expect(page).toHaveURL(/\/settings\/agents$/);

    await page.getByTestId("agents-tab-subagent").click();
    const roles = page.getByRole("list", { name: "Subagent roles" });
    await expect(roles.getByRole("button", { name: /Security/i })).toBeVisible();
    await expect(roles.getByRole("button", { name: /QA/i })).toBeVisible();
    await expect(roles.getByRole("button", { name: /Research/i })).toBeVisible();

    await page.getByTestId("agent-item-qa_verifier").click();
    await expect(page.getByTestId("agent-editor-title")).toHaveText(/QA/i);
    await expect(page.getByTestId("agent-editor")).toContainText("Subagent role");
    // A role runs inside its parent's session: no sandbox, access or test run of its own.
    await expect(page.getByTestId("agent-section-sandbox")).toHaveCount(0);
    await expect(page.getByTestId("agent-section-access")).toHaveCount(0);
    await expect(page.getByTestId("agent-test-run")).toBeDisabled();

    await page.getByTestId("agent-section-limits").click();
    await page.getByTestId("agent-max-turns").fill("6");
    await page.getByTestId("agent-section-tools").click();
    await expect(page.getByTestId("agent-tool-bash_exec")).toBeVisible();

    const saved = page.waitForResponse(
      (res) => res.url().includes("/v1/subagents/qa_verifier") && res.request().method() === "PUT"
    );
    await page.getByTestId("agent-save").click();
    expect((await (await saved).json()).max_turns).toBe(6);
    await expect(page.getByTestId("agent-save")).toHaveText(/Saved/);
    await page.request.delete("/v1/subagents/qa_verifier");
  });

  test("the model preference lives under Preferences, and the old page leads there", async ({ page }) => {
    await page.goto("/settings/user/models");
    await expect(page).toHaveURL(/\/settings\/user\/preferences$/);

    const mainContent = page.getByRole("main");
    await expect(mainContent.getByRole("heading", { level: 1, name: "Preferences" })).toBeVisible();

    // The searchable picker lists the models the API returns
    const picker = mainContent.getByTestId("preferred-model");
    await expect(picker).toBeEnabled();
    await picker.click();
    await expect(mainContent.getByRole("option").first()).toBeVisible();
    await mainContent.getByRole("option").nth(1).click();

    await expect(page.getByTestId("settings-unsaved")).toBeVisible();
    await page.getByTestId("settings-save-all").click();
    await expect(page.getByTestId("settings-save-bar")).toContainText("All changes saved.");
    await page.request.delete("/v1/settings/user_preferences/preferred_model");
  });
});
