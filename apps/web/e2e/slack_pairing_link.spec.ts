import AxeBuilder from "@axe-core/playwright";
import { test, expect, type Page } from "@playwright/test";

const SESSION = "slack_bot1_D1_1.0";

async function denySession(page: Page, context: { pairing_required: boolean; slack_user_id?: string }) {
  await page.route(new RegExp(`/v1/sessions/${SESSION}\\?tail=50`), (route) =>
    route.fulfill({ status: 403, json: { detail: "Forbidden: You do not have permission to access this session" } }),
  );
  await page.route(/\/v1\/slack\/pairing-context/, (route) => route.fulfill({ json: context }));
}

test.describe("opening a Slack guest's conversation", () => {
  test("offers to send the Slack user a pairing link", async ({ page }) => {
    await denySession(page, { pairing_required: true, slack_user_id: "U021XG96V5E" });
    let sent: unknown = null;
    await page.route(/\/v1\/slack\/pairing-requests/, async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: { status: "sent", slack_user_id: "U021XG96V5E" } });
    });

    await page.goto(`/chat/${SESSION}`);

    const prompt = page.getByTestId("session-access-prompt");
    await expect(prompt).toContainText("U021XG96V5E");
    await page.getByTestId("send-pairing-link").click();
    await expect(page.getByTestId("pairing-link-sent")).toBeVisible();
    expect(sent).toEqual({ session_id: SESSION });
    await expect(page.getByTestId("send-pairing-link")).toHaveCount(0);
  });

  test("says when a link was just sent", async ({ page }) => {
    await denySession(page, { pairing_required: true, slack_user_id: "U1" });
    await page.route(/\/v1\/slack\/pairing-requests/, (route) =>
      route.fulfill({ status: 429, json: { detail: "A link was sent a moment ago." } }),
    );

    await page.goto(`/chat/${SESSION}`);
    await page.getByTestId("send-pairing-link").click();

    await expect(page.getByTestId("pairing-link-error")).toContainText("a moment ago");
    await expect(page.getByTestId("send-pairing-link")).toBeEnabled();
  });

  test("offers nothing for a conversation that linking would not open", async ({ page }) => {
    await denySession(page, { pairing_required: false });

    await page.goto(`/chat/${SESSION}`);

    await expect(page.getByTestId("session-access-prompt")).toContainText("Ask its owner to share it");
    await expect(page.getByTestId("send-pairing-link")).toHaveCount(0);
  });

  for (const scheme of ["light", "dark"] as const) {
    test(`the prompt has no accessibility violations (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await denySession(page, { pairing_required: true, slack_user_id: "U1" });
      await page.goto(`/chat/${SESSION}`);
      await expect(page.getByTestId("send-pairing-link")).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      expect(results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))).toEqual([]);
    });
  }
});

test.describe("the pairing link", () => {
  test("shows which Slack user is linked, then links and opens the conversation", async ({ page }) => {
    await page.route(/\/v1\/slack\/pair\/preview/, (route) =>
      route.fulfill({ json: { slack_user_id: "U021XG96V5E", team_id: "T1" } }),
    );
    let linked: unknown = null;
    await page.route(/\/v1\/slack\/pair$/, async (route) => {
      linked = route.request().postDataJSON();
      await route.fulfill({ json: { status: "linked", slack_user_id: "U021XG96V5E", sessions_adopted: 18 } });
    });
    await page.route(new RegExp(`/v1/sessions/${SESSION}`), (route) => route.fulfill({ status: 404, json: { detail: "Session not found" } }));

    await page.goto(`/link-slack?code=ABCD2345&session=${SESSION}`);

    await expect(page.getByTestId("link-slack-summary")).toContainText("U021XG96V5E");
    // Nothing is linked by merely opening the page.
    expect(linked).toBeNull();
    await page.getByTestId("link-slack-confirm").click();
    await expect(page).toHaveURL(new RegExp(`/chat/${SESSION}$`));
    expect(linked).toEqual({ code: "ABCD2345" });
  });

  test("goes to the Slack settings when no conversation is named, or the name is not one", async ({ page }) => {
    await page.route(/\/v1\/slack\/pair\/preview/, (route) => route.fulfill({ json: { slack_user_id: "U1", team_id: "T1" } }));
    await page.route(/\/v1\/slack\/pair$/, (route) => route.fulfill({ json: { status: "linked" } }));

    await page.goto("/link-slack?code=ABCD2345&session=https://evil.example/x");
    await page.getByTestId("link-slack-confirm").click();

    await expect(page).toHaveURL(/\/settings\/slack$/);
  });

  test("reports a link that is dead or refused", async ({ page }) => {
    await page.route(/\/v1\/slack\/pair\/preview/, (route) =>
      route.fulfill({ status: 400, json: { detail: "That link is invalid or has expired" } }),
    );

    await page.goto("/link-slack?code=ZZZZ2222");

    await expect(page.getByTestId("link-slack-error")).toContainText("invalid or has expired");
    await expect(page.getByTestId("link-slack-confirm")).toHaveCount(0);
  });

  test("keeps the confirmation open when the account is already linked elsewhere", async ({ page }) => {
    await page.route(/\/v1\/slack\/pair\/preview/, (route) => route.fulfill({ json: { slack_user_id: "U1", team_id: "T1" } }));
    await page.route(/\/v1\/slack\/pair$/, (route) =>
      route.fulfill({ status: 409, json: { detail: "Your account is already linked to another Slack user." } }),
    );

    await page.goto("/link-slack?code=ABCD2345");
    await page.getByTestId("link-slack-confirm").click();

    await expect(page.getByTestId("link-slack-error")).toContainText("already linked");
    await expect(page.getByTestId("link-slack-confirm")).toBeEnabled();
  });

  test("a link without a code explains itself", async ({ page }) => {
    await page.goto("/link-slack");
    await expect(page.getByTestId("link-slack-page")).toContainText("no code");
  });

  for (const scheme of ["light", "dark"] as const) {
    test(`the confirmation has no accessibility violations (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.route(/\/v1\/slack\/pair\/preview/, (route) => route.fulfill({ json: { slack_user_id: "U1", team_id: "T1" } }));
      await page.goto("/link-slack?code=ABCD2345");
      await expect(page.getByTestId("link-slack-confirm")).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      expect(results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))).toEqual([]);
    });
  }
});
