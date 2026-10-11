import { test, expect } from "@playwright/test";

// The backend contract (hashing, lock-out, audit) is covered by tests/security/test_local_accounts.py;
// this proves the login form is wired to it. Password accounts need PostgreSQL, which the e2e
// stack does not run, so the two endpoints are stubbed at the network layer.
test.describe("Password sign-in form", () => {
  test("shows the form when enabled, reports a refusal, and honours a forced password change", async ({ page }) => {
    await page.route("**/api/auth/configured-providers", (route) =>
      route.fulfill({
        json: { google: false, github: false, oidc: false, devLogin: false, local: true },
      })
    );
    let attempts = 0;
    await page.route("**/api/auth/local/login", (route) => {
      attempts += 1;
      if (attempts === 1) {
        return route.fulfill({ status: 401, json: { detail: "Incorrect e-mail or password." } });
      }
      return route.fulfill({ json: { status: "authenticated", must_change_password: true } });
    });

    await page.goto("/login");
    const form = page.getByTestId("local-login");
    await expect(form).toBeVisible();

    await form.getByLabel("E-mail").fill("alice@acme.test");
    await form.getByLabel("Password").fill("not the right one");
    await form.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Incorrect e-mail or password.")).toBeVisible();

    await form.getByLabel("Password").fill("a temporary password");
    await form.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/settings\/user\/profile\?change_password=1/);
    await expect(page.getByTestId("change-password")).toBeVisible();
    await expect(page.getByText("An administrator issued you a temporary password")).toBeVisible();
  });

  test("hides the form when password sign-in is not enabled", async ({ page }) => {
    await page.route("**/api/auth/configured-providers", (route) =>
      route.fulfill({
        json: { google: false, github: false, oidc: false, devLogin: true, local: false },
      })
    );
    await page.goto("/login");
    await expect(page.getByTestId("local-login")).toHaveCount(0);
  });
});
