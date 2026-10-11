import { test, expect, type WebSocketRoute } from "@playwright/test";

/**
 * The deterministic backend's stub model cannot produce a real file diff, so this spec
 * injects one `file_diff` frame over the live WebSocket (forwarded to the real backend)
 * and then exercises the cockpit's per-file apply: the button posts the active file's
 * diff to `POST /v1/sessions/{id}/diffs/apply`, which patches the session workspace.
 * The patch engine itself is covered by `tests/integration/test_diffs_apply.py`.
 */
test.describe("Cockpit per-file diff apply", () => {
  test("applies a single file from a proposed multi-file diff", async ({ page, request }) => {
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: { title: "Diff Apply Mission" },
    });
    expect(createRes.ok()).toBeTruthy();
    const sessionId: string = (await createRes.json()).session_id;

    const diffPath = "src/added_by_diff.txt";
    const fileDiffFrame = {
      event_type: "file_diff",
      session_id: sessionId,
      payload: {
        path: diffPath,
        diff_content:
          "--- /dev/null\n+++ b/src/added_by_diff.txt\n@@ -0,0 +1,1 @@\n+hello from e2e\n",
        additions: 1,
        deletions: 0,
        is_new_file: true,
      },
    };

    let injected = false;
    await page.routeWebSocket(/\/v1\/sessions\/.*\/ws/, (ws: WebSocketRoute) => {
      const server = ws.connectToServer();
      server.onMessage((message) => {
        // Forward every real frame first, then inject the synthetic diff once the
        // handshake `sync` has arrived so the cockpit is ready to render it.
        ws.send(message);
        if (!injected && typeof message === "string" && message.includes('"sync"')) {
          injected = true;
          ws.send(JSON.stringify(fileDiffFrame));
        }
      });
    });

    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    // The diff matrix lives in the right-hand inspector panel, which is collapsed by
    // default; open it so the proposed diff and its apply control are mounted.
    await page.getByTestId("right-panel-toggle").click();

    const applyButton = page.getByTestId("apply-diff-button");
    await expect(applyButton).toBeVisible({ timeout: 15000 });
    await expect(applyButton).toHaveText(/Apply this file/);

    await applyButton.click();

    // The real endpoint applied the file, and the cockpit reflects it.
    await expect(page.getByTestId("diff-apply-status")).toContainText("Applied");
  });
});
