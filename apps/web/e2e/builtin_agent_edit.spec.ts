import { test, expect } from "@playwright/test";

/**
 * Built-in agents are editable by an administrator for the whole organization, and can be reset
 * to what ships. The local dev identity is an administrator.
 */
test("an administrator edits the built-in agent in place and resets it", async ({ page }) => {
  try {
    await page.goto("/settings/agents");
    await page.getByText("General Software Engineer").first().click();
    await expect(page.getByText("ID: persona-general")).toBeVisible();

    await page.getByLabel("Description").fill("Edited by the e2e administrator");
    const saved = page.waitForResponse(
      (res) => res.url().endsWith("/v1/agents") && res.request().method() === "POST"
    );
    await page.getByRole("button", { name: /save persona/i }).click();
    const body = await (await saved).json();
    expect(body.id).toBe("persona-general");
    expect(body.customized).toBe(true);

    await page.reload();
    await page.getByText("General Software Engineer").first().click();
    await expect(page.getByLabel("Description")).toHaveValue("Edited by the e2e administrator");

    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: /reset to default/i }).click();
    await expect(page.getByLabel("Description")).not.toHaveValue("Edited by the e2e administrator");
    await expect(page.getByRole("button", { name: /reset to default/i })).toHaveCount(0);
  } finally {
    await page.request.delete("/v1/agents/persona-general");
  }
});
