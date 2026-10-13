import { test, expect } from "@playwright/test";

/**
 * Permission modes are enforced by the platform, not requested of the model: in Ask mode a
 * shell command waits for a person before it runs. This drives the real backend with the
 * deterministic stub model (`[stub:bash]` makes it call `bash_exec`).
 */
test.describe("Permission modes", () => {
  test("Ask mode holds a command for approval and runs it once approved", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("permission-mode").selectOption("ask");
    await page.getByTestId("instruction-input").fill("[stub:bash] list the workspace");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });

    const card = page.getByTestId("approval-card");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card).toContainText("bash_exec");
    await expect(card).toContainText("echo stub-ran");

    await page.getByTestId("approval-approve").click();
    await expect(card).toBeHidden({ timeout: 10000 });
    await expect(page.getByText(/Stub answer/).first()).toBeVisible({ timeout: 15000 });
  });

  test("the agent picker still opens while a turn waits for approval", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("permission-mode").selectOption("ask");
    await page.getByTestId("instruction-input").fill("[stub:bash] list the workspace");
    await page.getByTestId("ignite-button").click();
    await expect(page.getByTestId("approval-card")).toBeVisible({ timeout: 15000 });

    await page.getByTitle("Select Active Language Model or Agent").click();
    await expect(page.getByText("Applies to your next message")).toBeVisible();
    await page.getByRole("button", { name: /Security & Penetration Auditor/i }).last().click();
    await expect(page.getByTitle("Select Active Language Model or Agent")).toContainText(/Security/);
  });

  test("a denied command never runs and the agent is told", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("permission-mode").selectOption("ask");
    await page.getByTestId("instruction-input").fill("[stub:bash] delete everything");
    await page.getByTestId("ignite-button").click();

    const card = page.getByTestId("approval-card");
    await expect(card).toBeVisible({ timeout: 15000 });
    await page.getByTestId("approval-reject").click();
    await expect(card).toBeHidden({ timeout: 10000 });
    await expect(page.getByText(/Denied by the user/).first()).toBeVisible({ timeout: 15000 });
  });

  test("Auto mode runs the same command without asking", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("permission-mode").selectOption("auto");
    await page.getByTestId("instruction-input").fill("[stub:bash] list the workspace");
    await page.getByTestId("ignite-button").click();

    await expect(page.getByText(/Stub answer/).first()).toBeVisible({ timeout: 20000 });
    await expect(page.getByTestId("approval-card")).toHaveCount(0);
  });

  test("the chosen mode is stored on the session", async ({ page, request }) => {
    await page.goto("/");
    await page.getByTestId("permission-mode").selectOption("plan");
    await page.getByTestId("instruction-input").fill("hello");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/(session_.+)/, { timeout: 15000 });
    const sessionId = page.url().split("/chat/")[1];

    const session = await (await request.get(`http://localhost:8000/v1/sessions/${sessionId}`)).json();
    expect(session.metadata.permission_mode).toBe("plan");
  });

  test("a reload while a command waits for approval brings the card back", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("permission-mode").selectOption("ask");
    await page.getByTestId("instruction-input").fill("[stub:bash] list the workspace");
    await page.getByTestId("ignite-button").click();

    const card = page.getByTestId("approval-card");
    await expect(card).toBeVisible({ timeout: 15000 });

    await page.reload();
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card).toContainText("bash_exec");

    await page.getByTestId("approval-approve").click();
    await expect(page.getByText(/Stub answer/).first()).toBeVisible({ timeout: 15000 });
  });
});
