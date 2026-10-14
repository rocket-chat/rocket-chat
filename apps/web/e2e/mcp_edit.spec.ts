import { test, expect, type Page } from "@playwright/test";

const API = "http://localhost:8000";

interface StoredServer {
  id: string;
  name: string;
  has_api_key: boolean;
  has_oauth_secret: boolean;
  header_prefix: string | null;
  oauth_client_id: string | null;
  oauth_scopes: string[];
}

async function storedServer(page: Page, name: string): Promise<StoredServer> {
  const servers: StoredServer[] = await (await page.request.get(`${API}/v1/mcp/servers`)).json();
  return servers.find((s) => s.name === name)!;
}

async function remove(page: Page, name: string): Promise<void> {
  const servers: StoredServer[] = await (await page.request.get(`${API}/v1/mcp/servers`)).json();
  for (const server of servers.filter((s) => s.name === name)) {
    await page.request.delete(`${API}/v1/mcp/servers/${server.id}`);
  }
}

test.describe("Editing remote MCP servers", () => {
  test("an organization server with an API key is edited without retyping the key", async ({ page }) => {
    const name = `E2E Remote ${Date.now()}`;
    const created = await page.request.post(`${API}/v1/mcp/servers`, {
      data: {
        name,
        transport: "http",
        endpoint_or_command: "https://mcp.example.test/a",
        scope: "org",
        tools: [{ name: "echo", description: "Echo" }],
        auth: { auth_type: "api_key", api_key: "remote-secret", header_name: "X-Api-Key", header_prefix: "Token" },
      },
    });
    expect(created.ok()).toBeTruthy();

    try {
      await page.goto("/settings/mcp");
      await page.getByRole("button", { name: `Edit ${name}` }).click();
      const dialog = page.getByTestId("mcp-server-modal");
      await expect(dialog.getByRole("heading", { name: `Edit ${name}` })).toBeVisible();
      await expect(page.getByLabel("Header Name")).toHaveValue("X-Api-Key");
      await expect(page.getByLabel("Prefix")).toHaveValue("Token");
      await expect(page.getByLabel("Secret API Key (Write-Only)")).toHaveAttribute("placeholder", /Unchanged/);

      await page.getByLabel("Prefix").fill("Key");
      await page.getByRole("button", { name: /Probe & connect server/i }).click();
      await expect(dialog).toBeHidden({ timeout: 30000 });

      const after = await storedServer(page, name);
      expect(after.header_prefix).toBe("Key");
      expect(after.has_api_key).toBe(true);
    } finally {
      await remove(page, name);
    }
  });

  test("a personal OAuth server keeps its client when its scopes change", async ({ page }) => {
    const name = `E2E Personal ${Date.now()}`;
    const created = await page.request.post(`${API}/v1/mcp/servers`, {
      data: {
        name,
        transport: "http",
        endpoint_or_command: "https://mcp.example.test/b",
        scope: "user",
        tools: [{ name: "echo", description: "Echo" }],
        auth: {
          auth_type: "oauth2",
          oauth_authorization_url: "https://auth.example.test/authorize",
          oauth_token_url: "https://auth.example.test/token",
          oauth_client_id: "client-1",
          oauth_client_secret: "client-secret",
          oauth_scopes: ["read"],
        },
      },
    });
    expect(created.ok()).toBeTruthy();

    try {
      await page.goto("/settings/mcp");
      await page.getByRole("button", { name: `Edit ${name}` }).click();
      await expect(page.getByLabel("Client ID")).toHaveValue("client-1");
      await expect(page.getByLabel("Client Secret (Optional for Public PKCE)")).toHaveAttribute(
        "placeholder",
        /Unchanged/
      );

      await page.getByLabel("Scopes (Space Separated)").fill("read write");
      await page.getByRole("button", { name: /Probe & connect server/i }).click();
      await expect(page.getByTestId("mcp-server-modal")).toBeHidden({ timeout: 30000 });

      const after = await storedServer(page, name);
      expect(after.oauth_scopes).toEqual(["read", "write"]);
      expect(after.oauth_client_id).toBe("client-1");
      expect(after.has_oauth_secret).toBe(true);
    } finally {
      await remove(page, name);
    }
  });
});
