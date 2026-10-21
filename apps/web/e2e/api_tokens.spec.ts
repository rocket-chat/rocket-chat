import { test, expect, type Page } from "@playwright/test";

/**
 * API tokens are created and revoked from the settings console. The token endpoints need the
 * PostgreSQL store, so they are answered here; the endpoints themselves are covered by the
 * backend tests.
 */
interface StubToken {
  id: string;
  name: string;
  user_id: string;
  guest: boolean;
  created_at: number;
  expires_at: number;
  last_used_at: number | null;
}

async function stubTokens(page: Page, seed: StubToken[]): Promise<{ requests: string[] }> {
  const tokens = [...seed];
  const requests: string[] = [];
  const json = (route: Parameters<Parameters<Page["route"]>[1]>[0], body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/v1/version", (route) => json(route, { version: "dev" }));
  await page.route(/\/v1\/(user|admin)\/api-tokens/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const guest = url.pathname.startsWith("/v1/admin");
    requests.push(`${request.method()} ${url.pathname}`);
    if (request.method() === "GET") return json(route, tokens);
    if (request.method() === "POST") {
      const body = request.postDataJSON() as { name: string };
      const created: StubToken = {
        id: `t${tokens.length + 1}`,
        name: body.name,
        user_id: guest ? "apitoken:new" : "dev",
        guest,
        created_at: 1_700_000_000,
        expires_at: 4_100_000_000,
        last_used_at: null,
      };
      tokens.push(created);
      return json(route, { ...created, token: "rcpat_demo.secret.value" }, 201);
    }
    const id = url.pathname.split("/").pop();
    tokens.splice(0, tokens.length, ...tokens.filter((t) => t.id !== id));
    return json(route, { revoked: true });
  });
  return { requests };
}

const member = (over: Partial<StubToken>): StubToken => ({
  id: "m1",
  name: "nightly script",
  user_id: "dev",
  guest: false,
  created_at: 1_700_000_000,
  expires_at: 4_100_000_000,
  last_used_at: null,
  ...over,
});

test("a member creates a personal token, copies it once and revokes it", async ({ page }) => {
  await stubTokens(page, []);
  await page.goto("/settings/user/api-tokens");

  await expect(page.getByRole("link", { name: "API reference" })).toHaveAttribute("href", /\/api\/rest$/);
  const personal = page.getByTestId("api-tokens-personal");
  await expect(personal.getByText("No personal tokens")).toBeVisible();

  await personal.getByLabel("Name").fill("ci runner");
  await personal.getByRole("button", { name: "Create token" }).click();
  await expect(personal.getByTestId("new-token")).toHaveText("rcpat_demo.secret.value");
  await expect(personal.getByRole("cell", { name: "ci runner", exact: true })).toBeVisible();

  await personal.getByRole("button", { name: "Revoke ci runner" }).click();
  await expect(personal.getByRole("cell", { name: "ci runner", exact: true })).toHaveCount(0);
  await expect(personal.getByTestId("new-token")).toHaveCount(0);
});

test("administrators manage guest tokens, and only guest ones are listed", async ({ page }) => {
  const { requests } = await stubTokens(page, [
    member({ id: "m1", name: "alice's script", user_id: "alice" }),
    member({ id: "g1", name: "support bot", user_id: "apitoken:g1", guest: true }),
  ]);
  await page.goto("/settings/user/api-tokens");

  const guest = page.getByTestId("api-tokens-guest");
  await expect(guest.getByRole("cell", { name: "support bot", exact: true })).toBeVisible();
  await expect(guest.getByRole("cell", { name: "alice's script", exact: true })).toHaveCount(0);

  await guest.getByRole("button", { name: "Revoke support bot" }).click();
  await expect(guest.getByRole("cell", { name: "support bot", exact: true })).toHaveCount(0);
  expect(requests).toContain("DELETE /v1/admin/api-tokens/g1");
});

test("the settings navigation shows the running version as it was built", async ({ page }) => {
  await stubTokens(page, []);
  await page.goto("/settings/user/api-tokens");
  await expect(page.getByTestId("app-version")).toHaveText("dev");

  for (const [built, shown] of [
    ["0.5.0", "v0.5.0"],
    ["dev-21102026-1036", "dev-21102026-1036"],
  ]) {
    await page.unroute("**/v1/version");
    await page.route("**/v1/version", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ version: built }) })
    );
    await page.reload();
    await expect(page.getByTestId("app-version")).toHaveText(shown);
  }
});

test("the API access page is listed in the personal settings", async ({ page }) => {
  await stubTokens(page, []);
  await page.goto("/settings/user/profile");
  await page.getByRole("navigation", { name: "Personal" }).getByRole("link", { name: /API access/ }).click();
  await expect(page.getByRole("heading", { name: "API access" })).toBeVisible();
});

test("the create form lines its controls up on one row", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await stubTokens(page, []);
  await page.goto("/settings/user/api-tokens");

  const personal = page.getByTestId("api-tokens-personal");
  const bottoms = await Promise.all(
    [personal.getByLabel("Name"), personal.getByLabel("Expires after"), personal.getByRole("button", { name: "Create token" })].map(
      async (control) => {
        const box = await control.boundingBox();
        return box!.y + box!.height;
      }
    )
  );
  expect(Math.max(...bottoms) - Math.min(...bottoms)).toBeLessThanOrEqual(1);
});

