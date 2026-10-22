import { test, expect } from "@playwright/test";

/**
 * The first message of a session lifts the brand's rocket out of the Run button. The flight is
 * drawn by a host in the root layout, because sending the message replaces the new-conversation
 * page (and its composer) with the session's: it must outlive that navigation.
 */
test.describe("Launch rocket", () => {
  test("the first message launches a rocket that outlives the page change and then lands", async ({ page }) => {
    await page.goto("/chat/draft");
    await page.getByTestId("instruction-input").fill("Say hello");
    await page.getByTestId("ignite-button").click();

    const rocket = page.getByTestId("launch-rocket");
    await expect(rocket).toBeAttached({ timeout: 3000 });
    // It is still flying after the conversation page has replaced the composer it left.
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    await expect(rocket).toBeAttached();
    // And it ends by itself, leaving nothing behind.
    await expect(rocket).toHaveCount(0, { timeout: 5000 });
  });

  test("nothing is drawn for people who ask for reduced motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/chat/draft");
    await page.evaluate(() => {
      (window as unknown as { __launchSeen: number }).__launchSeen = 0;
      new MutationObserver(() => {
        if (document.querySelector('[data-testid="launch-rocket"]')) {
          (window as unknown as { __launchSeen: number }).__launchSeen += 1;
        }
      }).observe(document.body, { childList: true, subtree: true });
    });
    await page.getByTestId("instruction-input").fill("Say hello");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    await page.waitForTimeout(1500);

    expect(await page.evaluate(() => (window as unknown as { __launchSeen: number }).__launchSeen)).toBe(0);
  });
});
