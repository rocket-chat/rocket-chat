import { test, expect } from "@playwright/test";

/**
 * The form must send credentials in the shape the API reads; it used to send a flat payload the
 * API silently ignored, so keys and OAuth settings were never saved.
 */
test("the MCP form saves credentials and per-member OAuth mode", async ({ page }) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route(/\/v1\/mcp\/servers$/, async (route) => {
    if (route.request().method() === "POST") {
      bodies.push(route.request().postDataJSON());
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "srv-e2e", name: "E2E Linear", transport: "http", endpoint_or_command: "https://mcp.example/mcp",
          scope: "org", auth_type: "oauth2", status: "connected", tools_count: 0, tools: [],
          oauth_mode: "member", oauth_status: "pending",
        }),
      });
      return;
    }
    await route.fallback();
  });

  await page.goto("/settings/mcp");
  await page.getByRole("button", { name: /Add org server/i }).click();
  await page.getByPlaceholder("e.g. Stripe Billing MCP").fill("E2E Linear");
  await page.getByPlaceholder("https://api.internal.service/mcp").fill("https://mcp.example/mcp");
  await page.locator("select").filter({ hasText: /OAuth 2\.1/ }).first().selectOption("oauth2");
  await page.getByTestId("mcp-oauth-member-mode").check();
  await page.getByRole("button", { name: /PROBE & CONNECT SERVER/i }).click();

  await expect.poll(() => bodies.length).toBe(1);
  const auth = (bodies[0] as { auth: Record<string, unknown> }).auth;
  expect(auth.auth_type).toBe("oauth2");
  expect(auth.oauth_mode).toBe("member");
  expect(bodies[0]).not.toHaveProperty("auth_config");
});
