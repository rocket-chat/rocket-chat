import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

const API = "http://localhost:8000";

// What a real compaction summary looks like: sections, nested lists, a few thousand characters.
const SUMMARY = [
  "1. Goal & Stop Condition",
  "   - Implement the ticket: save the stream position regularly.",
  "",
  "2. Decisions Made",
  ...Array.from({ length: 60 }, (_, i) => `   - Decision ${i + 1}: a reasonably long line explaining why this was chosen over the alternatives.`),
  "",
  "3. Files Touched",
  "   - `history/service/watcher/watcher.go`: heartbeat loop and per-collection failure isolation.",
].join("\n");

test.describe("Compaction card", () => {
  test("shows what happened in one line and folds the long summary away", async ({ page, request }) => {
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Compaction" } });
    const sessionId: string = (await created.json()).session_id;
    const socket = await routeSessionSocket(page, sessionId);
    await page.setViewportSize({ width: 1280, height: 800 });

    await page.goto(`/chat/${sessionId}`);
    await socket.ready;
    socket.emit("context_compacted", {
      compacted_messages_count: 273,
      tokens_before: 108826,
      tokens_after: 22431,
      summary: SUMMARY,
    });

    const card = page.getByTestId("compaction-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Context Compacted");
    await expect(page.getByTestId("compaction-headline")).toHaveText(
      "Compressed 273 messages from 108,826 to 22,431 tokens."
    );

    // Folded by default: the summary is not there, so the card stays short and wide.
    await expect(page.getByTestId("compaction-summary")).toHaveCount(0);
    const folded = await card.boundingBox();
    expect(folded!.height).toBeLessThan(140);
    expect(folded!.width).toBeGreaterThan(560);

    const toggle = page.getByTestId("compaction-toggle");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    const summary = page.getByTestId("compaction-summary");
    await expect(summary).toContainText("Decisions Made");
    // However long, it scrolls inside the card instead of stretching the conversation.
    const box = await summary.boundingBox();
    expect(box!.height).toBeLessThanOrEqual(330);
    expect(await summary.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);

    await toggle.click();
    await expect(page.getByTestId("compaction-summary")).toHaveCount(0);
  });

  test("says when the summary had to be rebuilt from the tool history", async ({ page, request }) => {
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Digest" } });
    const sessionId: string = (await created.json()).session_id;
    const socket = await routeSessionSocket(page, sessionId);

    await page.goto(`/chat/${sessionId}`);
    await socket.ready;
    socket.emit("context_compacted", {
      compacted_messages_count: 196,
      tokens_before: 114312,
      tokens_after: 35968,
      summary: "(Rebuilt from the tool history.)\n\n## Files changed\n- `history/config/config.go`",
      summary_status: "digest",
    });

    await expect(page.getByTestId("compaction-headline")).toHaveText(
      "Compressed 196 messages from 114,312 to 35,968 tokens. " +
        "The model's summary was unusable, so it was rebuilt from the tool history."
    );
  });

  test("a message that merely mentions compaction is not shown as one", async ({ page, request }) => {
    const created = await request.post(`${API}/v1/sessions`, { data: { title: "Not a compaction" } });
    const sessionId: string = (await created.json()).session_id;
    const socket = await routeSessionSocket(page, sessionId);

    await page.goto(`/chat/${sessionId}`);
    await socket.ready;
    socket.emit("continuation_scheduled", {
      continuation_index: 1,
      max_continuations: 3,
      reason: "The context was compacted twice and tasks remain",
    });

    await expect(page.getByTestId("continuation-card")).toBeVisible();
    await expect(page.getByTestId("compaction-card")).toHaveCount(0);
  });
});
