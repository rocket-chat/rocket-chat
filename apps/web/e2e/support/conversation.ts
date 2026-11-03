import { expect, type Page } from "@playwright/test";

/**
 * Opens a conversation and waits until it has opened. Until its record arrives the composer is
 * read-only (keystrokes are dropped) and per-conversation panel state is reset on arrival, so a
 * spec that types or opens a panel sooner races the load, which is slowest on PostgreSQL.
 */
export async function openConversation(page: Page, sessionId: string): Promise<void> {
  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByTestId("instruction-input")).toBeEditable();
}
