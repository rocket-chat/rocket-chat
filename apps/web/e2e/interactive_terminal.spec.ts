import { test, expect } from "@playwright/test";

// The PTY plumbing (auth, ACL, shell I/O, resize, audit) is covered by tests/security/test_terminal.py
// against the real WebSocket handler. This proves the cockpit's Shell tab speaks that protocol:
// binary keystrokes out, binary output in, JSON control, and the server's refusal shown to the user.
async function openShellTab(page: import("@playwright/test").Page) {
  // The shell belongs to a real session (a draft has no sandbox), so create one first.
  const created = await page.request.post("/v1/sessions", { data: { title: "terminal e2e" } });
  const { session_id: sessionId } = await created.json();
  await page.goto(`/chat/${sessionId}`);
  await page.getByTitle(/Expand Right Panel|Show Panel/i).click();
  await page.getByRole("button", { name: "SHELL" }).click();
}

test.describe("Interactive terminal (Shell tab)", () => {
  test("types into a shell and shows what comes back", async ({ page }) => {
    const received: string[] = [];
    await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/terminal/, (ws) => {
      ws.send(Buffer.from("rocket$ "));
      ws.onMessage((message) => {
        if (typeof message === "string") return; // resize control frames
        received.push(Buffer.from(message).toString());
        ws.send(message); // a tty echoes what is typed
      });
    });

    await openShellTab(page);

    const terminal = page.getByTestId("interactive-terminal");
    await expect(terminal.locator(".xterm-rows")).toContainText("rocket$", { timeout: 10000 });

    await terminal.locator(".xterm").click();
    await page.keyboard.type("ls -la");
    await expect(terminal.locator(".xterm-rows")).toContainText("ls -la");
    expect(received.join("")).toContain("ls -la");
  });

  test("shows why the server refused the terminal and offers to reconnect", async ({ page }) => {
    await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/terminal/, (ws) => {
      ws.send(
        JSON.stringify({
          type: "error",
          code: "sandbox_not_running",
          detail: "The sandbox is not running. Send a message to start it, then open the terminal.",
        })
      );
      ws.close({ code: 1008 });
    });

    await openShellTab(page);

    const terminal = page.getByTestId("interactive-terminal");
    await expect(terminal.getByText(/The sandbox is not running/)).toBeVisible({ timeout: 10000 });
    await expect(terminal.getByRole("button", { name: "Reconnect" })).toBeVisible();
  });
});
