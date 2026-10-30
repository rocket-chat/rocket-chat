import { test, expect, type Page } from "@playwright/test";

/**
 * Artifacts are shown the way their type calls for, and an artifact the agent updates offers its
 * earlier versions. The artifact endpoints are answered here so each type can be shown without
 * running a model; the endpoints themselves are covered by the backend tests.
 */
function artifactsFor(sessionId: string) {
  return [
    { id: "a_md", title: "Migration plan", filename: "plan.md", artifact_type: "markdown", version: 1, content: "# Plan heading\n\nStep one.", session_id: sessionId, created_at: 1, updated_at: 1 },
    { id: "a_html", title: "Landing mock", filename: "mock.html", artifact_type: "html", version: 1, content: "<h1>Hello frame</h1>", session_id: sessionId, created_at: 2, updated_at: 2 },
    { id: "a_json", title: "Config", filename: "config.json", artifact_type: "json", version: 1, content: '{"a":1,"b":[1,2]}', session_id: sessionId, created_at: 3, updated_at: 3 },
    { id: "a_diagram", title: "Flow", filename: "flow.mermaid", artifact_type: "diagram", version: 1, content: "graph TD\n  A[Start] --> B[Finish]", session_id: sessionId, created_at: 4, updated_at: 4 },
    { id: "a_diff", title: "Patch", filename: "change.diff", artifact_type: "diff", version: 2, content: "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n", session_id: sessionId, created_at: 5, updated_at: 6 },
  ];
}

async function stubArtifacts(page: Page, sessionId: string): Promise<void> {
  const artifacts = artifactsFor(sessionId);
  const prefix = `/v1/sessions/${sessionId}/artifacts`;
  await page.route(`**${prefix}**`, async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.slice(url.pathname.indexOf(prefix) + prefix.length);
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "" || path === "/") {
      return json(artifacts.map((artifact) => ({ ...artifact, content: undefined })));
    }
    const [, id, tail] = path.split("/");
    const artifact = artifacts.find((a) => a.id === id);
    if (!artifact) return route.fulfill({ status: 404, body: "{}" });
    if (tail === "versions") {
      return json(
        artifact.version > 1
          ? [
              { version: 1, created_at: 5, size: 20 },
              { version: 2, created_at: 6, size: artifact.content.length },
            ]
          : [{ version: 1, created_at: artifact.created_at, size: artifact.content.length }],
      );
    }
    if (url.searchParams.get("version") === "1") {
      return json({ ...artifact, version: 1, content: "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-first\n+draft\n" });
    }
    return json(artifact);
  });
}

test("each artifact type is shown the way it should be", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "Artifacts Mission" } });
  expect(created.ok()).toBeTruthy();
  const sessionId: string = (await created.json()).session_id;
  await stubArtifacts(page, sessionId);

  await page.goto(`/chat/${sessionId}`);
  await page.getByTestId("view-menu-trigger").click();
  await page.getByTestId("artifacts-drawer-toggle").click();
  // One place for artifacts: the menu opens the inspector on its Artifacts tab.
  const drawer = page.getByRole("tabpanel", { name: "Artifacts" });
  await expect(drawer).toBeVisible();

  await drawer.getByRole("button", { name: /Migration plan/ }).click();
  await expect(drawer.getByRole("heading", { name: "Plan heading" })).toBeVisible();

  await drawer.getByRole("button", { name: /Landing mock/ }).click();
  const frame = drawer.getByTestId("artifact-html");
  await expect(frame).toHaveAttribute("sandbox", "");
  await expect(page.frameLocator('[data-testid="artifact-html"]').getByText("Hello frame")).toBeVisible();

  await drawer.getByRole("button", { name: /Config/ }).click();
  await expect(drawer.getByTestId("artifact-json")).toContainText('"a": 1');
  await expect(drawer.getByTestId("artifact-json")).toContainText('"b": [');

  await drawer.getByRole("button", { name: /Flow/ }).click();
  await expect(drawer.getByTestId("artifact-diagram")).toContainText("Start", { timeout: 20000 });
  await expect(drawer.getByTestId("artifact-diagram").locator("svg")).toBeVisible();
});

test("an updated artifact offers its earlier versions", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "Versions Mission" } });
  const sessionId: string = (await created.json()).session_id;
  await stubArtifacts(page, sessionId);

  await page.goto(`/chat/${sessionId}`);
  await page.getByTestId("view-menu-trigger").click();
  await page.getByTestId("artifacts-drawer-toggle").click();
  // One place for artifacts: the menu opens the inspector on its Artifacts tab.
  const drawer = page.getByRole("tabpanel", { name: "Artifacts" });

  await drawer.getByRole("button", { name: /Patch/ }).click();
  await expect(drawer.getByTestId("inline-diff")).toContainText("+new");

  const versions = drawer.getByTestId("artifact-version");
  await expect(versions.locator("option")).toHaveText(["v2 (current)", "v1"]);
  await versions.selectOption("1");
  await expect(drawer.getByTestId("inline-diff")).toContainText("+draft");
  await versions.selectOption("2");
  await expect(drawer.getByTestId("inline-diff")).toContainText("+new");
});
