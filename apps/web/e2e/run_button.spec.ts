import { test, expect } from "@playwright/test";

/** A button must never be white text on a transparent background (it vanished on a white card). */
for (const scheme of ["light", "dark"] as const) {
  test(`the Run button is filled and readable in ${scheme} mode`, async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: scheme });
    const page = await context.newPage();
    await page.goto("/");
    await page.getByTestId("instruction-input").fill("hello");

    const colors = await page.getByTestId("ignite-button").evaluate((el) => {
      const style = getComputedStyle(el);
      return { background: style.backgroundColor, text: style.color };
    });

    expect(colors.background).not.toBe("rgba(0, 0, 0, 0)");
    expect(colors.background).not.toBe(colors.text);
    await context.close();
  });
}
