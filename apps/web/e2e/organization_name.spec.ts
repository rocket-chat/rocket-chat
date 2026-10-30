import { test, expect } from "@playwright/test";

// Renaming needs the database, which the in-memory e2e stack does not run: the route is stubbed
// (tests/integration/test_admin_users.py covers the API, its audit and who may call it).
test("an administrator renames the organization from its General settings", async ({ page }) => {
  let name = "Acme";
  const renames: unknown[] = [];
  await page.route("**/v1/organization", async (route) => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as { name: string };
      renames.push(body);
      name = body.name;
    }
    await route.fulfill({ json: { id: "default_org", name } });
  });

  await page.goto("/settings/org");
  const card = page.getByTestId("organization-name");
  const input = card.getByLabel("Name");
  await expect(input).toHaveValue("Acme");
  await expect(card.getByTestId("organization-name-save")).toBeDisabled();

  await input.fill("  Acme Platform ");
  await card.getByTestId("organization-name-save").click();

  await expect(card.getByRole("status")).toHaveText("Renamed.");
  await expect(input).toHaveValue("Acme Platform");
  expect(renames).toEqual([{ name: "Acme Platform" }]);
});
