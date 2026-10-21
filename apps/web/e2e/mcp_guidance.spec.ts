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
    await register(page, name, GUIDANCE);
    try {
      await page.goto("/settings/mcp");
      const trigger = page.getByTestId("mcp-guidance-trigger").first();
      await expect(trigger).toHaveText("Use for billing questions");

      await page.getByTestId("mcp-guidance-card-toggle").first().click();
      const body = page.getByTestId("mcp-guidance-body").first();
      await expect(body.getByRole("heading", { name: "Use for billing questions" })).toBeVisible();
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
});
