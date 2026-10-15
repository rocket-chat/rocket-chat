import { test, expect, type WebSocketRoute } from "@playwright/test";

const APPROVAL_FRAME = {
  event_type: "approval_required",
  payload: {
    action_id: "ap_e2e_1",
    action_type: "git_revert_hard",
    command_or_path: "Discard uncommitted work",
    risk_level: "critical",
  },
};

/**
 * Opens a session with the telemetry socket intercepted, returning a sender used
 * to push frames once the cockpit has finished bootstrapping the session.
 */
async function openSessionAndConnect(
  page: import("@playwright/test").Page,
  prompt: string,
): Promise<{ socket: WebSocketRoute; decisions: string[] }> {
  const decisions: string[] = [];
  let socket: WebSocketRoute | null = null;

  await page.routeWebSocket(/\/ws/, (ws) => {
    socket = ws;
    ws.onMessage((message) => {
      try {
        const data = JSON.parse(message.toString());
        if (data.type === "approval") {
          decisions.push(`${data.action_id}:${data.approved}`);
        }
      } catch {
        // Non-JSON frames are irrelevant for this assertion.
      }
    });
    ws.send(JSON.stringify({ type: "sync" }));
  });

  await page.goto("/");
  await page.getByTestId("instruction-input").fill(prompt);
  await page.getByTestId("ignite-button").click();
  await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });

  if (!socket) {
    throw new Error("The cockpit did not open a telemetry WebSocket");
  }
  return { socket, decisions };
}

/**
 * Re-emits the approval frame until the card is rendered. The cockpit resets its
 * session-scoped state once the session snapshot finishes loading, so a single
 * frame sent immediately after navigation can be discarded.
 */
async function emitApprovalUntilVisible(
  socket: WebSocketRoute,
  card: import("@playwright/test").Locator,
): Promise<void> {
  await expect
    .poll(
      async () => {
        socket.send(JSON.stringify(APPROVAL_FRAME));
        return card.count();
      },
      { timeout: 15000, intervals: [200, 200, 400, 600, 1000] },
    )
    .toBeGreaterThan(0);
}

test.describe("Human approval gate", () => {
  test("renders the approval card and submits the decision over WebSocket", async ({ page }) => {
    const { socket, decisions } = await openSessionAndConnect(
      page,
      "Approve the destructive action",
    );

    const card = page.getByTestId("approval-card");
    await emitApprovalUntilVisible(socket, card);

    await expect(card).toBeVisible();
    await expect(card).toContainText("git_revert_hard");
    await expect(card).toContainText("critical risk");

    await page.getByTestId("approval-approve").click();
    await expect.poll(() => decisions.length, { timeout: 10000 }).toBeGreaterThan(0);
    expect(decisions[0]).toBe("ap_e2e_1:true");

    // The gate clears optimistically once a decision has been submitted.
    await expect(card).toBeHidden({ timeout: 10000 });
  });

  test("submits a rejection when the user declines", async ({ page }) => {
    const { socket, decisions } = await openSessionAndConnect(page, "Reject the action");

    const card = page.getByTestId("approval-card");
    await emitApprovalUntilVisible(socket, card);
    await expect(card).toBeVisible();

    await page.getByTestId("approval-reject").click();
    await expect.poll(() => decisions.length, { timeout: 10000 }).toBeGreaterThan(0);
    expect(decisions[0]).toBe("ap_e2e_1:false");
  });

  test("withdraws the card when the turn ends without a decision", async ({ page }) => {
    const { socket, decisions } = await openSessionAndConnect(page, "Cancel while waiting");

    const card = page.getByTestId("approval-card");
    await emitApprovalUntilVisible(socket, card);
    await expect(card).toBeVisible();

    socket.send(
      JSON.stringify({
        event_type: "status_changed",
        payload: { status: "cancelled", message: "Cancelled", final: true },
      }),
    );

    await expect(card).toBeHidden({ timeout: 10000 });
    expect(decisions).toEqual([]);
  });
});
