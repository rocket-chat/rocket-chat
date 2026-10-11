import { test, expect } from "@playwright/test";

test("browse the workspace and save an edit that the API (and so the agent) sees", async ({
  page,
  request,
}) => {
  // [stub:tool] makes the agent list the workspace, which starts the (fake) sandbox.
  await page.goto("/");
  await page.getByTestId("instruction-input").fill("[stub:tool] look around");
  await page.getByTestId("ignite-button").click();
  await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
  const sessionId = page.url().split("/chat/")[1];
  await expect(page.getByText(/Stub answer/).first()).toBeVisible({ timeout: 20000 });

  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-files").click();

  const browser = page.getByTestId("workspace-browser");
  await expect(browser.getByTestId("ws-folder").filter({ hasText: "src" })).toBeVisible({ timeout: 15000 });
  await browser.getByTestId("ws-folder").filter({ hasText: "src" }).click();
  await browser.getByTestId("ws-file").filter({ hasText: "util.py" }).click();
  await expect(page.getByTestId("ws-current")).toHaveText(/util\.py/);

  // Monaco is served by this app (/monaco/vs), not a CDN, so it loads offline too.
  const editor = browser.locator(".monaco-editor").first();
  await expect(editor).toBeVisible({ timeout: 30000 });
  await editor.click();
  await page.keyboard.press("ControlOrMeta+End");
  await page.keyboard.type("\n# edited in the browser\n");
  await page.getByTestId("ws-save").click();
  await expect(browser.getByRole("status")).toHaveText("Saved.");

  const file = await (
    await request.get(`http://localhost:8000/v1/sessions/${sessionId}/workspace/file`, {
      params: { path: "src/util.py" },
    })
  ).json();
  expect(file.content).toContain("# edited in the browser");
});
