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
  await page.getByTestId("mcp-next").click();
  await page.getByPlaceholder("e.g. Stripe Billing MCP").fill("E2E Linear");
  await page.getByPlaceholder("https://api.internal.service/mcp").fill("https://mcp.example/mcp");
  await page.getByTestId("mcp-next").click();
  await page.getByLabel("Authentication Mode").selectOption("oauth2");
  await page.getByTestId("mcp-oauth-member-mode").check();
  const signIn = page.context().waitForEvent("page");
  await page.getByRole("button", { name: /Add & sign in/i }).click();
  await (await signIn).close();

  await expect.poll(() => bodies.length).toBe(1);
  const auth = (bodies[0] as { auth: Record<string, unknown> }).auth;
  expect(auth.auth_type).toBe("oauth2");
  expect(auth.oauth_mode).toBe("member");
  expect(bodies[0]).not.toHaveProperty("auth_config");
});


/**
 * A URL is enough: the form asks the server whether it signs people in, switches to OAuth with
 * nothing to fill in, and saving opens the sign-in in its own tab.
 */
test("a server that signs people in needs only its URL", async ({ page }) => {
  await page.route(/\/v1\/mcp\/servers\/validate$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        valid: false, status: "needs_sign_in", tools: [], sign_in: "automatic",
        error: "This server signs you in with your account: save it, then sign in.",
      }),
    })
  );
  await page.route(/\/v1\/mcp\/servers$/, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "srv-kb", name: "Knowledge base", transport: "http", endpoint_or_command: "https://kb.example/mcp",
        scope: "user", auth_type: "oauth2", status: "needs_sign_in", tools_count: 0, tools: [], oauth_status: "pending",
      }),
    });
  });

  await page.goto("/settings/mcp");
  await page.getByRole("button", { name: /^Add server/i }).click();
  await page.getByTestId("mcp-next").click();
  await page.getByPlaceholder("e.g. Stripe Billing MCP").fill("Knowledge base");
  const url = page.getByPlaceholder("https://api.internal.service/mcp");
  await url.fill("https://kb.example/mcp");
  await url.blur();
  await expect(page.getByTestId("mcp-save-label")).toHaveText("Add & sign in");
  await page.getByTestId("mcp-next").click();

  await expect(page.getByLabel("Authentication Mode")).toHaveValue("oauth2");
  await expect(page.getByTestId("mcp-sign-in-summary")).toContainText("Nothing to set up");
  await expect(page.getByLabel("Client ID")).toHaveCount(0);

  const signIn = page.context().waitForEvent("page");
  await page.getByRole("button", { name: /Add & sign in/i }).click();
  const tab = await signIn;
  await expect.poll(() => tab.url()).toContain("/v1/mcp/servers/srv-kb/oauth/authorize");
  await tab.close();
});

test("the outcome of a sign-in is shown when it lands on the settings page", async ({ page }) => {
  await page.goto("/settings/mcp?status=connected&server_id=srv-kb");
  await expect(page.getByText(/Signed in: the server's tools are available/)).toBeVisible();
  await expect(page).toHaveURL(/\/settings\/mcp$/);
});
