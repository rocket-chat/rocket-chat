import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

const PAGES = [
  "/",
  "/missions",
  "/settings",
  "/settings/mcp",
  "/settings/agents",
  "/settings/skills",
  "/settings/knowledge",
  "/settings/slack",
  "/settings/org",
  "/settings/user",
  "/settings/user/api-tokens",
];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`WCAG 2.1 AA (${scheme})`, () => {
    test.use({ colorScheme: scheme });
    for (const path of PAGES) {
      test(`${path} has no serious or critical violations`, async ({ page }) => {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
        const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        expect(blocking.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
      });
    }
  });
}

test.describe("mobile cockpit", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("the cockpit does not scroll horizontally on a phone", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
