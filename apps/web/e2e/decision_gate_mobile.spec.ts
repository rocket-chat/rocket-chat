import { test, expect, type WebSocketRoute } from "@playwright/test";

const DECISION_FRAME = {
  event_type: "decision_required",
  payload: {
    question_id: "q_e2e_1",
    question_text: "Which repository should I clone, and with which credentials should I authenticate?",
    options: [
      "Clone birotaio/backend-services with my linked GitHub account",
      "Clone it with the GitHub App installation token",
      "Skip cloning and continue without the repository",
      "Ask me for a different repository",
    ],
    is_multi_select: false,
    default_recommended_option: "Clone it with the GitHub App installation token",
  },
};

test.describe("Decision gate on an iPhone", () => {
  test.use({ viewport: { width: 402, height: 874 }, isMobile: true, hasTouch: true });

  test("the card keeps its full height and its confirm button can be reached and tapped", async ({ page }) => {
    let socket: WebSocketRoute | null = null;
    const answers: string[] = [];
    await page.routeWebSocket(/\/ws/, (ws) => {
      socket = ws;
      ws.onMessage((message) => {
        try {
          const data = JSON.parse(message.toString());
          if (data.type === "answer_question") answers.push(JSON.stringify(data));
        } catch {
          // frames this test does not care about
        }
      });
      ws.send(JSON.stringify({ type: "sync" }));
    });

    await page.goto("/");
    await page.getByTestId("instruction-input").fill("hello");
    await page.getByTestId("ignite-button").tap();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });

    const card = page.getByTestId("decision-card");
    await expect
      .poll(
        async () => {
          socket?.send(JSON.stringify(DECISION_FRAME));
          return card.count();
        },
        { timeout: 15000, intervals: [200, 200, 400, 600, 1000] },
      )
      .toBeGreaterThan(0);

    // Nothing inside the card may be clipped: a compressed card hides its options and button.
    const clipped = await card.evaluate((el) => el.scrollHeight - el.clientHeight);
    expect(clipped).toBeLessThanOrEqual(1);

    const confirm = card.getByRole("button", { name: /confirm decision/i });
    await confirm.scrollIntoViewIfNeeded();
    await expect(confirm).toBeInViewport();
    await confirm.tap();
    await expect.poll(() => answers.length).toBeGreaterThan(0);
  });
});
