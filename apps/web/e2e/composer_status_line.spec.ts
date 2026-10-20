import { test, expect } from "@playwright/test";

test.describe("Composer", () => {
  test("shows one status line and names the permission modes plainly", async ({ page }) => {
    await page.goto("/");
    const mode = page.getByTestId("permission-mode");
    await expect(mode).toBeVisible();
    await expect(mode.locator("option")).toHaveText(["Org default", "Read-only", "Ask first", "Auto-edit", "Auto", "No approvals"]);
    await expect(page.getByTestId("agent-model-picker")).toBeVisible();
    await expect(page.getByTestId("sandbox-state")).toContainText("Sandbox starts when needed");
    await expect(page.getByTestId("instruction-input")).toHaveAttribute("placeholder", "What should we work on?");
  });

  test("Run is clearly off until there is something to send", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("ignite-button")).toBeDisabled();
    await page.getByTestId("instruction-input").fill("hello");
    await expect(page.getByTestId("ignite-button")).toBeEnabled();
  });

  test("the shortcuts open on their own once, then on request", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId("composer-hints")).toBeVisible();
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toHaveCount(0);
    await page.getByTestId("composer-hints").click();
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toContainText("Point at a file or repository");
  });

  test("@ offers the workspace's files and inserts the one chosen", async ({ page, request }) => {
    const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "Mentions" } });
    const sessionId: string = (await created.json()).session_id;
    await page.route(`**/v1/sessions/${sessionId}/workspace/tree**`, async (route) => {
      const path = new URL(route.request().url()).searchParams.get("path");
      const entries =
        path === "api"
          ? [{ name: "main.py", path: "/workspace/api/main.py", is_dir: false, size_bytes: 10 }]
          : [
              { name: "api", path: "/workspace/api", is_dir: true, size_bytes: 0 },
              { name: "web", path: "/workspace/web", is_dir: true, size_bytes: 0 },
            ];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ path: path ?? ".", entries, truncated: false }) });
    });

    await page.goto(`/chat/${sessionId}`);
    const input = page.getByTestId("instruction-input");
    await input.click();
    await input.pressSequentially("look at @a");

    const menu = page.getByTestId("mention-menu");
    await expect(menu).toContainText("api/");
    await expect(menu).not.toContainText("web");
    await page.keyboard.press("Enter");
    await expect(input).toHaveValue("look at @api/");

    await expect(menu).toContainText("api/main.py");
    await page.keyboard.press("Tab");
    await expect(input).toHaveValue("look at @api/main.py ");
  });

  test("dragging files over the composer says what dropping does", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("composer").dispatchEvent("dragover", { dataTransfer: await page.evaluateHandle(() => new DataTransfer()) });
    await expect(page.getByTestId("drop-hint")).toBeVisible();
  });
});
