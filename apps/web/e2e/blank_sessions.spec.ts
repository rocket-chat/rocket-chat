import { test, expect } from "@playwright/test";

test.describe("Blank sessions and GitHub settings", () => {
  test("a session starts blank and ignores a repository sent at creation", async ({ request }) => {
    const res = await request.post("http://localhost:8000/v1/sessions", {
      data: { git_repo: "https://github.com/acme/widgets", git_branch: "develop" },
    });
    expect(res.ok()).toBeTruthy();
    const session = await res.json();
    expect(session.git_repo).toBeNull();
    expect(session.git_branch).toBeNull();
  });

  test("the new-session screen offers no repository picker", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("instruction-input")).toBeVisible();
    await expect(page.getByTestId("repo-toggle")).toHaveCount(0);
  });

  test("the GitHub settings page lists mapped installations and offers the install link", async ({
    page,
  }) => {
    await page.route(/\/v1\/admin\/github\/installations$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          app_configured: true,
          install_url: "https://github.com/apps/rocket/installations/new?state=abc",
          installations: [{ installation_id: 42, account_login: "acme" }],
        }),
      }),
    );
    await page.goto("/settings/github");
    const panel = page.getByTestId("github-installations");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("github-installation")).toContainText("acme");
    await expect(panel.getByTestId("github-install-link")).toHaveAttribute(
      "href",
      /github\.com\/apps\/rocket\/installations\/new/,
    );
  });
});
