import { test, expect } from "@playwright/test";

/**
 * The Changes tab lists what the session changed in each repository (read from the workspace),
 * switches between the whole session and the latest turn, and undoes one file after a
 * confirmation. The endpoints are answered here; they are covered with real git by the backend
 * tests (`tests/unit/test_workspace_changes.py`).
 */
test("the Changes tab lists files, switches scope and undoes one file", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "Changes Mission" } });
  const sessionId: string = (await created.json()).session_id;

  const requests: string[] = [];
  const reverted: unknown[] = [];
  let sessionFiles = [
    { path: "src/app.py", status: "modified", additions: 3, deletions: 1, binary: false },
    { path: "README.md", status: "added", additions: 10, deletions: 0, binary: false },
  ];

  await page.route(`**/v1/sessions/${sessionId}/changes?**`, async (route) => {
    const scope = new URL(route.request().url()).searchParams.get("scope");
    requests.push(`list:${scope}`);
    const files = scope === "turn" ? sessionFiles.slice(0, 1) : sessionFiles;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        available: true,
        scope,
        repositories: [
          {
            path: "/workspace/app",
            name: "app",
            base_label: scope === "turn" ? "since the start of the latest turn" : "since it branched from main",
            files,
            truncated: false,
          },
        ],
      }),
    });
  });
  await page.route(`**/v1/sessions/${sessionId}/changes/file?**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ original: "def run():\n    return 1\n", modified: "def run():\n    return 2\n", binary: false, truncated: false }),
    });
  });
  await page.route(`**/v1/sessions/${sessionId}/changes/revert`, async (route) => {
    reverted.push(route.request().postDataJSON());
    sessionFiles = sessionFiles.filter((file) => file.path !== "src/app.py");
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "reverted" }) });
  });

  await page.goto(`/chat/${sessionId}`);
  await page.getByTestId("right-panel-toggle").click();
  await page.getByTestId("tab-changes").click();

  const files = page.getByTestId("changes-files");
  await expect(files).toContainText("src/app.py", { timeout: 15000 });
  await expect(files).toContainText("README.md");
  await expect(page.getByTestId("changes-summary")).toContainText("2 files changed");
  await expect(page.getByTestId("changes-base")).toContainText("since it branched from main");

  await page.getByTestId("changes-scope-turn").click();
  await expect(page.getByTestId("changes-base")).toContainText("since the start of the latest turn");
  await expect(files).not.toContainText("README.md");
  await page.getByTestId("changes-scope-session").click();
  await expect(files).toContainText("README.md");

  await files.getByRole("button", { name: /src\/app\.py/ }).click();
  await page.getByTestId("changes-revert").click();
  await page.getByTestId("changes-revert-confirm").click();

  await expect.poll(() => reverted.length).toBe(1);
  expect(reverted[0]).toMatchObject({ repository: "/workspace/app", path: "src/app.py", scope: "session" });
  await expect(files).not.toContainText("src/app.py");
});

test("a sandbox that is not running is said so", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "Asleep Mission" } });
  const sessionId: string = (await created.json()).session_id;
  await page.route(`**/v1/sessions/${sessionId}/changes?**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ available: false, scope: "session", repositories: [] }),
    });
  });

  await page.goto(`/chat/${sessionId}`);
  await page.getByTestId("right-panel-toggle").click();
  await page.getByTestId("tab-changes").click();

  await expect(page.getByTestId("changes-empty")).toContainText("The sandbox is not running", { timeout: 15000 });
});
