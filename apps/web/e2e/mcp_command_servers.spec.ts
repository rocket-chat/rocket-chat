import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

const API = "http://localhost:8000";
const ENV_SERVER = path.resolve(__dirname, "../../../tests/support/env_mcp_server.py");

async function openOrgForm(page: Page): Promise<void> {
  await page.goto("/settings/mcp");
  await page.getByRole("button", { name: /Add org server/i }).click();
  await expect(page.getByTestId("mcp-server-modal")).toBeVisible();
  await page.getByLabel("Transport", { exact: true }).selectOption("stdio");
}

async function fillVariable(page: Page, index: number, name: string, value: string, opts: { secret?: boolean; member?: boolean } = {}) {
  await page.getByTestId("env-add").click();
  await page.getByLabel(`Variable ${index} name`).fill(name);
  await page.getByLabel(`Variable ${index} value`).fill(value);
  const row = page.getByTestId("env-row").nth(index - 1);
  const [secret, member] = await row.getByRole("checkbox").all();
  if (opts.secret) await secret.check();
  else await secret.uncheck();
  if (opts.member) await member.check();
}

async function removeServer(page: Page, name: string): Promise<void> {
  const servers: { id: string; name: string }[] = await (await page.request.get(`${API}/v1/mcp/servers`)).json();
  for (const server of servers.filter((s) => s.name === name)) {
    await page.request.delete(`${API}/v1/mcp/servers/${server.id}`);
  }
}

test.describe("Command (stdio) MCP servers on the MCP runner", () => {
  test("an administrator adds a command server; a member sets their own key", async ({ page }) => {
    const name = `E2E Grafana ${Date.now()}`;
    await openOrgForm(page);
    await page.getByPlaceholder("e.g. Stripe Billing MCP").fill(name);
    await page.getByPlaceholder(/server-postgres/).fill(`python3 ${ENV_SERVER}`);
    await fillVariable(page, 1, "BASE_URL", "https://grafana.example");
    await fillVariable(page, 2, "API_KEY", "org-readonly-key", { secret: true, member: true });
    await page.getByRole("button", { name: /Probe & connect server/i }).click();

    // The runner really started the command and listed its tool.
    const card = page.locator("div.rounded-lg", { hasText: name }).first();
    await expect(card).toBeVisible({ timeout: 30000 });
    await card.getByRole("button", { name: /Expand server details/ }).click();
    await expect(card.getByText("whoami")).toBeVisible({ timeout: 15000 });
    await expect(card.getByTestId("command-details")).toContainText("Members can set their own API_KEY");

    const mine = card.getByTestId("member-env-API_KEY");
    await expect(mine).toHaveAttribute("placeholder", /organization's value/);
    await mine.fill("my-own-key");
    await card.getByTestId("member-env-save").click();
    await expect(card.getByText("Saved.")).toBeVisible();
    await expect(mine).toHaveAttribute("placeholder", /Set - type to replace/);

    await card.getByTestId("member-env-clear-API_KEY").click();
    await expect(mine).toHaveAttribute("placeholder", /organization's value/);
    await removeServer(page, name);
  });

  test("an install script provides a program that is not an npx or uvx package", async ({ page }) => {
    const name = `E2E Binary ${Date.now()}`;
    await openOrgForm(page);
    await page.getByPlaceholder("e.g. Stripe Billing MCP").fill(name);
    await page.getByPlaceholder(/server-postgres/).fill("fake-env-mcp");
    await page.getByTestId("mcp-install-toggle").click();
    await page
      .getByTestId("mcp-install-command")
      .fill(`printf '#!/bin/sh\\nexec python3 ${ENV_SERVER}\\n' > fake-env-mcp\nchmod +x fake-env-mcp`);

    await page.getByRole("button", { name: /TEST & VALIDATE CONNECTION/i }).click();
    await expect(page.getByText("Connection Verified")).toBeVisible({ timeout: 30000 });
    await page.getByRole("button", { name: /Probe & connect server/i }).click();
    await expect(page.locator("div.rounded-lg", { hasText: name }).first()).toBeVisible({ timeout: 30000 });
    await removeServer(page, name);
  });

  test("a broken command is explained with its own error", async ({ page }) => {
    await openOrgForm(page);
    await page.getByPlaceholder("e.g. Stripe Billing MCP").fill("Broken command");
    await page.getByPlaceholder(/server-postgres/).fill("echo 'package not found' >&2; exit 3");
    await page.getByRole("button", { name: /TEST & VALIDATE CONNECTION/i }).click();
    await expect(page.getByText("Validation Failed")).toBeVisible({ timeout: 30000 });
    await expect(page.getByText(/package not found/)).toBeVisible();
  });

  test("without a reachable runner, commands are disabled with the reason", async ({ page }) => {
    await page.route("**/v1/mcp/runner", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ configured: false, available: false, detail: "The MCP runner is not deployed for this installation." }),
      }),
    );
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /Add org server/i }).click();

    await expect(page.locator("option[value=stdio]")).toBeDisabled();
    await page.getByTestId("mcp-preset").focus();
    await expect(page.locator("optgroup[label*='Commands'] option").first()).toBeDisabled();
  });

  test("a member is told to ask an administrator for command servers", async ({ page }) => {
    await page.route("**/api/auth/me", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "m", user_id: "member_user", email: "m@example.com", name: "Member", org_id: "default_org", roles: ["developer"] }),
      }),
    );
    await page.goto("/settings/mcp");
    await page.getByRole("button", { name: /Add server/i }).click();

    await expect(page.locator("option[value=stdio]")).toBeDisabled();
    await expect(page.getByText(/added by an administrator - ask yours/)).toBeVisible();
  });

  test("an administrator edits an organization command server and keeps its secret", async ({ page }) => {
    const name = `E2E Editable ${Date.now()}`;
    const created = await page.request.post(`${API}/v1/mcp/servers`, {
      data: {
        name,
        transport: "stdio",
        endpoint_or_command: `python3 ${ENV_SERVER}`,
        scope: "org",
        auth: {
          auth_type: "custom_headers",
          headers: [
            { name: "BASE_URL", value: "https://grafana.example", is_secret: false },
            { name: "API_KEY", value: "org-readonly-key", is_secret: true, member_override: false },
            { name: "TEAM_KEY", value: "team-default", is_secret: true, member_override: true },
          ],
        },
      },
    });
    expect(created.ok()).toBeTruthy();

    try {
      await page.goto("/settings/mcp");
      await page.getByRole("button", { name: `Edit ${name}` }).click();
      const dialog = page.getByTestId("mcp-server-modal");
      await expect(dialog.getByRole("heading", { name: `Edit ${name}` })).toBeVisible();
      await expect(page.getByLabel("Variable 2 value")).toHaveAttribute("placeholder", /Unchanged/);
      await expect(page.getByLabel("Variable 3 value")).toHaveAttribute("placeholder", /Unchanged/);

      await page.getByLabel("Variable 1 value").fill("https://grafana.example/v2");
      await page.getByRole("button", { name: /Probe & connect server/i }).click();
      await expect(dialog).toBeHidden({ timeout: 30000 });

      const servers: { name: string; headers_preview: { name: string; value: string }[] }[] = await (
        await page.request.get(`${API}/v1/mcp/servers`)
      ).json();
      const after = servers.find((s) => s.name === name)!;
      expect(after.headers_preview.find((h) => h.name === "BASE_URL")?.value).toBe("https://grafana.example/v2");
      // Never shown, but still there: saving without retyping it did not blank it.
      expect(after.headers_preview.find((h) => h.name === "API_KEY")?.value).toBeTruthy();
      // The organization's default for a variable members may override is kept too.
      expect(after.headers_preview.find((h) => h.name === "TEAM_KEY")?.value).toBeTruthy();
    } finally {
      await removeServer(page, name);
    }
  });
});
