import { test, expect } from "@playwright/test";

test.describe("GitHub account linking", () => {
  test("GitHub connect button targets the OAuth linking flow", async ({ page }) => {
    await page.goto("/settings/github");

    const connect = page.getByRole("link", { name: /Connect with GitHub/i });
    // The link only renders while no GitHub account is linked.
    if (await connect.count()) {
      const href = await connect.first().getAttribute("href");
      expect(href).toBe("/api/auth/github/login?link_only=true&redirect_uri=/settings/github");
    }
  });
});
