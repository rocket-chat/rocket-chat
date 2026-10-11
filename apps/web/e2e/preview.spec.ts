import { test, expect } from "@playwright/test";

/** The e2e sandbox is a fake without a network, so the preview endpoints are stubbed; the proxy itself is covered by tests/unit/test_preview.py and a real container. */
test("the preview tab embeds the app in a sandboxed frame", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Preview Mission" },
  });
  const sessionId: string = (await created.json()).session_id;

  const requested: unknown[] = [];
  await page.route(/\/v1\/sessions\/[^/]+\/preview$/, async (route) => {
    requested.push(route.request().postDataJSON());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ url: "/v1/preview/e2e-token/", port: 4173, expires_at: 9999999999 }),
    });
  });
  await page.route(/\/v1\/preview\/e2e-token\//, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body><h1 id='app'>Hello from the sandbox app</h1></body></html>",
    }),
  );

  await page.goto(`/chat/${sessionId}`);
  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-preview").click();

  await page.getByTestId("preview-port").fill("4173");
  await page.getByTestId("preview-open").click();

  const frame = page.getByTestId("preview-frame");
  await expect(frame).toBeVisible();
  expect(requested).toEqual([{ port: 4173 }]);
  await expect(page.frameLocator("[data-testid=preview-frame]").locator("#app")).toHaveText(
    "Hello from the sandbox app",
  );

  const sandbox = (await frame.getAttribute("sandbox")) ?? "";
  expect(sandbox).toContain("allow-scripts");
  expect(sandbox).not.toContain("allow-same-origin");
});

test("a port outside the allowed range is explained", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Preview Port" },
  });
  const sessionId: string = (await created.json()).session_id;
  await page.goto(`/chat/${sessionId}`);
  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-preview").click();
  await page.getByTestId("preview-port").fill("80");
  await page.getByTestId("preview-open").click();
  await expect(page.getByTestId("preview-error")).toContainText("1024");
});
