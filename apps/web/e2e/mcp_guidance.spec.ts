import { test, expect, type Page } from "@playwright/test";

const API = "http://localhost:8000";

const GUIDANCE = "## Use for billing questions\n\nAlways **confirm the currency** first.\n\n- Check the customer\n- Never refund without approval";

async function remove(page: Page, name: string): Promise<void> {
  const servers: { id: string; name: string }[] = await (await page.request.get(`${API}/v1/mcp/servers`)).json();
  for (const server of servers.filter((s) => s.name === name)) {
    await page.request.delete(`${API}/v1/mcp/servers/${server.id}`);
  }
}

async function register(page: Page, name: string, guidance: string): Promise<void> {
  const created = await page.request.post(`${API}/v1/mcp/servers`, {
    data: {
      name,
      transport: "http",
      endpoint_or_command: "https://mcp.example.test/billing",
      scope: "org",
      guidance,
      tools: [{ name: "refund", description: "Refund an invoice" }],
    },
  });
  expect(created.ok()).toBeTruthy();
}

test.describe("MCP server guidance", () => {
  test("a server's card shows the one line the agent weighs it by, and the manual rendered as markdown", async ({ page }) => {
    const name = `E2E Guided ${Date.now()}`;
    // Other specs leave guided servers behind: this one's text is unique so only its card matches.
    const trigger = `Use for billing questions ${Date.now()}`;
    await register(page, name, `## ${trigger}\n\nAlways **confirm the currency** first.\n\n- Check the customer\n- Never refund without approval`);
    try {
      await page.goto("/settings/mcp");
      // One card is open at a time and the page opens the first server's once the list has
      // loaded: let that settle, then open this server's details.
      const card = page.locator(`[data-testid="mcp-server-card"][data-server-name="${name}"]`);
      await expect(page.locator('[data-testid="mcp-server-card"] [aria-expanded="true"]')).toHaveCount(1);
      const expand = card.getByRole("button", { name: "Expand server details" });
      if (await expand.count()) await expand.click();
      await expect(card.getByRole("button", { name: "Collapse server details" })).toBeVisible();
      await expect(card.getByTestId("mcp-guidance-trigger")).toHaveText(trigger);

      await card.getByTestId("mcp-guidance-card-toggle").click();
      const body = card.getByTestId("mcp-guidance-body");
      await expect(body.getByRole("heading", { name: trigger })).toBeVisible();
      await expect(body.getByRole("listitem")).toHaveCount(2);
      await expect(body.locator("strong")).toHaveText("confirm the currency");
    } finally {
      await remove(page, name);
    }
  });

  test("the editor writes markdown, previews it, and tells what the agent will see", async ({ page }) => {
    const name = `E2E Editor ${Date.now()}`;
    await register(page, name, "");
    try {
      await page.goto("/settings/mcp");
      await page.getByRole("button", { name: `Edit ${name}` }).click();
      await page.getByTestId("mcp-step-tools").click();
      await page.getByTestId("mcp-guidance-toggle").click();

      await expect(page.getByTestId("guidance-trigger")).toContainText("adds nothing to the agent's context");
      await page.getByTestId("guidance-template").click();
      await expect(page.getByTestId("guidance-input")).toHaveValue(/^Use for <what this server is good at/);

      await page.getByTestId("guidance-input").fill(GUIDANCE);
      await expect(page.getByTestId("guidance-trigger")).toContainText("“Use for billing questions”");
      await expect(page.getByTestId("guidance-count")).toHaveAttribute("data-state", "short");

      await page.getByTestId("guidance-tab-preview").click();
      const preview = page.getByTestId("guidance-preview");
      await expect(preview.getByRole("heading", { name: "Use for billing questions" })).toBeVisible();
      await expect(preview.locator("strong")).toHaveText("confirm the currency");

      await page.getByTestId("guidance-tab-write").click();
      await page.getByTestId("guidance-input").fill(`Use for billing.\n${"Read the rules. ".repeat(120)}`);
      await expect(page.getByTestId("guidance-count")).toHaveAttribute("data-state", "long");
      await expect(page.getByTestId("guidance-editor")).toContainText("Past 1,500 characters");
    } finally {
      await remove(page, name);
    }
  });

  test("guidance over the limit is refused with the size, not saved", async ({ page }) => {
    const name = `E2E Limit ${Date.now()}`;
    await register(page, name, "");
    try {
      await page.goto("/settings/mcp");
      await page.getByRole("button", { name: `Edit ${name}` }).click();
      await page.getByTestId("mcp-step-tools").click();
      await page.getByTestId("mcp-guidance-toggle").click();
      await page.getByTestId("guidance-input").fill("x".repeat(8001));
      await expect(page.getByTestId("guidance-count")).toHaveAttribute("data-state", "over");

      await page.getByRole("button", { name: /Probe & connect server/i }).click();
      await expect(page.getByText(/limit is 8,000/)).toBeVisible();

      const servers: { name: string; guidance: string }[] = await (await page.request.get(`${API}/v1/mcp/servers`)).json();
      expect(servers.find((s) => s.name === name)?.guidance).toBe("");
    } finally {
      await remove(page, name);
    }
  });

  test("testing a server's connection starts the guidance from its own instructions", async ({ page }) => {
    const instructions = "Always confirm the currency before refunding.\n\nPrefer partial refunds.";
    await page.route("**/v1/mcp/servers/validate", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ valid: true, status: "connected", tools: [], instructions }),
      })
    );
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /Add server/i }).click();
    await page.getByTestId("mcp-next").click();
    await page.getByPlaceholder("e.g. Stripe Billing MCP").fill("E2E Instructed");
    await page.getByPlaceholder("https://api.internal.service/mcp").fill("https://instructed.example.test/mcp");

    await page.getByTestId("mcp-step-tools").click();
    await page.getByTestId("mcp-test-connection").click();

    await expect(page.getByTestId("guidance-input")).toHaveValue(/^Always confirm the currency/);
    await expect(page.getByTestId("guidance-notice")).toContainText("the server's own instructions");
    await expect(page.getByTestId("guidance-trigger")).toContainText("Always confirm the currency before refunding.");

    // Edited, it is the administrator's text: another test of the connection does not replace it.
    await page.getByTestId("guidance-input").fill("Mine.");
    await expect(page.getByTestId("guidance-notice")).toHaveCount(0);
    await page.getByTestId("mcp-test-connection").click();
    await expect(page.getByTestId("guidance-input")).toHaveValue("Mine.");
  });

  test("instructions longer than the limit are cut, and the page says so", async ({ page }) => {
    await page.route("**/v1/mcp/servers/validate", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ valid: true, status: "connected", tools: [], instructions: "x".repeat(9000) }),
      })
    );
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /Add server/i }).click();
    await page.getByTestId("mcp-next").click();
    await page.getByPlaceholder("https://api.internal.service/mcp").fill("https://long.example.test/mcp");

    await page.getByTestId("mcp-step-tools").click();
    await page.getByTestId("mcp-test-connection").click();

    await expect(page.getByTestId("guidance-notice")).toContainText("9,000 characters, cut to the 8,000 allowed");
    await expect(page.getByTestId("guidance-count")).toHaveText("8,000 / 8,000");
    await expect(page.getByTestId("guidance-count")).not.toHaveAttribute("data-state", "over");
  });
});

