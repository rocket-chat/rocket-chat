import { test, expect } from "@playwright/test";

/** The e2e sandbox is a fake, so the process endpoints are stubbed; the tools and the tracker run against a real shell in tests/unit. */
test("the Processes tab lists background processes, shows a log and opens a port in Preview", async ({
  page,
  request,
}) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Processes Mission" },
  });
  const sessionId: string = (await created.json()).session_id;

  let running = true;
  const stopped: string[] = [];
  await page.route(/\/v1\/sessions\/[^/]+\/processes$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        sandbox_running: true,
        processes: [
          {
            process_id: "pa1b2c3",
            name: "dev server",
            command: "npm run dev",
            state: running ? "running" : "stopped",
            port: 4173,
            exit_code: null,
            started_at: 1,
            ended_at: null,
          },
          {
            process_id: "pd4e5f6",
            name: "tests",
            command: "pytest -q",
            state: "exited",
            port: null,
            exit_code: 1,
            started_at: 2,
            ended_at: 3,
          },
        ],
      }),
    }),
  );
  await page.route(/\/v1\/sessions\/[^/]+\/processes\/pa1b2c3\/output/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ process_id: "pa1b2c3", output: "ready on http://localhost:4173" }),
    }),
  );
  await page.route(/\/v1\/sessions\/[^/]+\/processes\/pa1b2c3\/stop$/, (route) => {
    stopped.push("pa1b2c3");
    running = false;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ process_id: "pa1b2c3", message: "Stopped pa1b2c3." }),
    });
  });
  const previewRequests: unknown[] = [];
  await page.route(/\/v1\/sessions\/[^/]+\/preview$/, (route) => {
    previewRequests.push(route.request().postDataJSON());
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ url: "/v1/preview/e2e-token/", port: 4173, expires_at: 9999999999 }),
    });
  });
  await page.route(/\/v1\/preview\/e2e-token\//, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body><h1 id='app'>Served from a background process</h1></body></html>",
    }),
  );

  await page.goto(`/chat/${sessionId}`);
  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-processes").click();

  const rows = page.getByTestId("process-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first().getByTestId("process-state")).toHaveText(/running/i);
  await expect(rows.nth(1).getByTestId("process-state")).toHaveText(/exited 1/i);

  await rows.first().getByTestId("process-log-toggle").click();
  await expect(page.getByTestId("process-log")).toContainText("ready on http://localhost:4173");

  await rows.first().getByTestId("process-open-preview").click();
  await expect(page.getByTestId("preview-frame")).toBeVisible();
  // React strict mode runs the opening effect twice in development.
  expect(previewRequests.length).toBeGreaterThan(0);
  expect(previewRequests.every((body) => JSON.stringify(body) === '{"port":4173}')).toBe(true);
  await expect(page.frameLocator("[data-testid=preview-frame]").locator("#app")).toHaveText(
    "Served from a background process",
  );

  await page.getByTestId("tab-processes").click();
  await page.getByTestId("process-stop").first().click();
  await expect.poll(() => stopped).toEqual(["pa1b2c3"]);
  await expect(page.getByTestId("process-row").first().getByTestId("process-state")).toHaveText(
    /stopped/i,
    { timeout: 10000 },
  );
});

test("the Processes tab says so when nothing runs", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Empty Processes" },
  });
  const sessionId: string = (await created.json()).session_id;
  await page.route(/\/v1\/sessions\/[^/]+\/processes$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ sandbox_running: false, processes: [] }),
    }),
  );
  await page.goto(`/chat/${sessionId}`);
  await page.getByRole("button", { name: /Expand Right Panel/ }).click();
  await page.getByTestId("tab-processes").click();
  await expect(page.getByTestId("processes-empty")).toBeVisible();
});
