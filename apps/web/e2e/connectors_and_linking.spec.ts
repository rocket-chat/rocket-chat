import { test, expect } from "@playwright/test";

test.describe("Connector catalog & GitHub account linking", () => {
  test("GitHub connect button targets the OAuth linking flow", async ({ page }) => {
    await page.goto("/settings/github");

    const connect = page.getByRole("link", { name: /CONNECT GITHUB OAUTH/i });
    // The link only renders while no GitHub account is linked.
    if (await connect.count()) {
      const href = await connect.first().getAttribute("href");
      expect(href).toBe("/api/auth/github/login?link_only=true&redirect_uri=/settings/github");
    }
  });

  test("connector catalog lists productivity connectors with auth modes", async ({ page }) => {
    await page.goto("/settings/mcp");

    const main = page.getByRole("main");
    await expect(main.getByText(/CONNECTOR CATALOG/i)).toBeVisible({ timeout: 10000 });
    await expect(main.getByText("Notion", { exact: true })).toBeVisible();
    await expect(main.getByText("Jira & Confluence (Atlassian)", { exact: true })).toBeVisible();
    await expect(main.getByText("Google Drive & Workspace", { exact: true })).toBeVisible();

    // Auth-mode badges communicate org-wide vs per-user support.
    await expect(main.getByText(/Org-wide/i).first()).toBeVisible();
    await expect(main.getByText(/Per-user/i).first()).toBeVisible();
  });
});
