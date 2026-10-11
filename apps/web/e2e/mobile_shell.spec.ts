import { test, expect, type Page } from "@playwright/test";

/**
 * On a phone the application shell must never become scrollable: the document is exactly the
 * visible size, nothing is wider than the screen, and the top bar stays on screen, also after a
 * field takes focus (which makes browsers scroll the page to reveal it).
 */
test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

const BACKEND = "http://localhost:8000";

async function createSession(page: Page): Promise<string> {
  const res = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Mobile shell" } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).session_id as string;
}

async function expectShellIsStable(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => {
    const doc = document.scrollingElement as HTMLElement;
    return {
      scrollHeight: doc.scrollHeight,
      scrollWidth: doc.scrollWidth,
      innerHeight: window.innerHeight,
      innerWidth: window.innerWidth,
    };
  });
  expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.innerHeight);
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.innerWidth);

  // A programmatic scroll (what a focus or a drag does) must have nothing to move.
  await page.evaluate(() => window.scrollTo(0, 200));
  expect(await page.evaluate(() => window.scrollY)).toBe(0);

  const bar = await page.locator("header").first().boundingBox();
  expect(bar).not.toBeNull();
  expect(bar!.y).toBeGreaterThanOrEqual(0);
}

test.describe("Application shell on a phone", () => {
  for (const route of ["/", "/settings", "/settings/org", "/settings/mcp", "/missions"]) {
    test(`${route} fits the screen`, async ({ page }) => {
      await page.goto(route);
      await expect(page.locator("header").first()).toBeVisible();
      await expectShellIsStable(page);
    });
  }

  test("a conversation fits the screen and keeps its top bar when the field is focused", async ({
    page,
  }) => {
    const sessionId = await createSession(page);
    await page.goto(`/chat/${sessionId}`);
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeVisible();

    await input.tap();
    await expectShellIsStable(page);
  });

  test("the shell leaves room for the keyboard and gives it back when focus leaves", async ({
    page,
  }) => {
    await page.goto("/");
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeVisible();
    const bottomOf = async () => (await input.boundingBox())!.y + (await input.boundingBox())!.height;
    const resting = await bottomOf();

    // What ViewportSync publishes while a field is focused and the keyboard covers 320px.
    await input.focus();
    await page.evaluate(() => document.documentElement.style.setProperty("--keyboard-inset", "320px"));
    const height = page.viewportSize()!.height;
    await expect.poll(bottomOf).toBeLessThanOrEqual(height - 320);
    expect((await page.locator("header").first().boundingBox())!.y).toBeGreaterThanOrEqual(0);

    // Focus leaving always clears it, so a keyboard that closes silently cannot leave it stuck.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await expect
      .poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue("--keyboard-inset")))
      .toBe("0px");
    await expect.poll(bottomOf).toBeCloseTo(resting, 0);
  });

  test("a page the browser panned is put back when a field loses focus", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("header").first()).toBeVisible();

    // What iOS leaves behind after the keyboard closes: a document that has been scrolled.
    await page.evaluate(() => {
      const spacer = document.createElement("div");
      spacer.style.cssText = "position:absolute;top:0;left:0;width:1px;height:3000px";
      document.body.appendChild(spacer);
      window.scrollTo(0, 300);
    });
    await page.evaluate(() => window.dispatchEvent(new FocusEvent("focusout")));

    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  });

  test("touch fields never render below 16px (iOS would zoom the page)", async ({ page }) => {
    await page.goto("/settings/org");
    const fields = page.locator("input:not([type=checkbox]):not([type=radio]):not([type=range]), select, textarea");
    await expect(fields.first()).toBeVisible();
    const sizes = await fields.evaluateAll((nodes) =>
      nodes
        .filter((node) => (node as HTMLElement).offsetParent !== null)
        .map((node) => parseFloat(getComputedStyle(node).fontSize)),
    );
    expect(sizes.length).toBeGreaterThan(0);
    for (const size of sizes) expect(size).toBeGreaterThanOrEqual(16);
  });
});
