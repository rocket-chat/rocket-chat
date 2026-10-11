import { test, expect, type Page } from "@playwright/test";

/**
 * Attachment upload flow.
 *
 * A screenshot pasted or picked in the composer must become a visible chip with a
 * thumbnail, and a rejected file must fail visibly rather than disappear. The model
 * round-trip is asserted by the backend integration suite; this spec owns the cockpit.
 */

const PNG_BYTES = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4" +
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400" +
    "00000049454e44ae426082",
  "hex"
);

async function createSession(request: Page["request"], title: string): Promise<string> {
  const res = await request.post("http://localhost:8000/v1/sessions", {
    data: { title, initial_prompt: `${title} bootstrap turn` },
  });
  expect(res.ok()).toBeTruthy();
  const data = await res.json();
  return data.session_id as string;
}

async function openLoadedSession(page: Page, sessionId: string, prompt: string): Promise<void> {
  await page.goto(`/chat/${sessionId}`);
  await expect(page).toHaveURL(`/chat/${sessionId}`);
  // Waiting for the rehydrated transcript is the deterministic proof that the session
  // record (not just the URL) has loaded into the store before we upload against it.
  await expect(page.getByTestId("user-message").filter({ hasText: prompt })).toBeVisible({
    timeout: 15000,
  });
}

test.describe("Attachment upload", () => {
  test.describe.configure({ mode: "serial" });

  test("stages an image with a thumbnail and removes it", async ({ page, request }) => {
    const sessionId = await createSession(request, "Attachment Upload Mission");
    await openLoadedSession(page, sessionId, "bootstrap turn");

    await page.getByTestId("attachment-input").setInputFiles({
      name: "screenshot.png",
      mimeType: "image/png",
      buffer: PNG_BYTES,
    });

    const chip = page.getByTestId("attachment-chip").filter({ hasText: "screenshot.png" });
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute("data-status", "ready");
    // An image preview renders as a data URL thumbnail.
    await expect(chip.locator("img")).toBeVisible();

    // Removing the chip clears it from the staging tray.
    await chip.getByTestId("attachment-remove").click();
    await expect(page.getByTestId("attachment-chip")).toHaveCount(0);
  });

  test("carries a staged log attachment into the dispatched turn", async ({ page, request }) => {
    const sessionId = await createSession(request, "Attachment Dispatch Mission");
    await openLoadedSession(page, sessionId, "bootstrap turn");

    await page.getByTestId("attachment-input").setInputFiles({
      name: "server.log",
      mimeType: "text/plain",
      buffer: Buffer.from("ERROR boom\nTraceback line\n", "utf-8"),
    });

    const chip = page.getByTestId("attachment-chip").filter({ hasText: "server.log" });
    await expect(chip).toHaveAttribute("data-status", "ready");

    const input = page.getByTestId("instruction-input");
    await input.fill("what failed in this log?");
    await page.getByTestId("ignite-button").click();

    // The optimistic user bubble shows the attachment it carried, proving the composer
    // associated the upload with *this* turn rather than dropping it.
    const ownMessage = page.getByTestId("user-message").filter({ hasText: "what failed in this log?" });
    await expect(ownMessage).toBeVisible();
    await expect(
      ownMessage.getByTestId("user-attachments").filter({ hasText: "server.log" })
    ).toBeVisible();
  });

  test("shows a visible failure for a rejected file type", async ({ page, request }) => {
    const sessionId = await createSession(request, "Attachment Rejection Mission");
    await openLoadedSession(page, sessionId, "bootstrap turn");

    await page.getByTestId("attachment-input").setInputFiles({
      name: "payload.exe",
      mimeType: "application/x-msdownload",
      buffer: Buffer.from("MZ", "utf-8"),
    });

    const failedChip = page.getByTestId("attachment-chip").filter({ hasText: "payload.exe" });
    await expect(failedChip).toHaveAttribute("data-status", "error");
  });
});
