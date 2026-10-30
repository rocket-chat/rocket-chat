import { test, expect } from "@playwright/test";

test("an administrator reads the audit log and filters it by event type", async ({ page }) => {
  await page.route("**/v1/admin/audit*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        events: [
          { id: "1", event_type: "settings_change", actor_user_id: "u1", session_id: null, tool_name: "slack", risk_level: null, approved_by: null, result_summary: "updated", created_at: 1700000000, redacted: false, redaction_count: 0 },
          { id: "2", event_type: "tool_call", actor_user_id: "u1", session_id: "s", tool_name: "bash_exec", risk_level: "high", approved_by: null, result_summary: "ok", created_at: 1700000100, redacted: true, redaction_count: 2 },
        ],
        next_cursor: null,
        retention: { audit_days: 90 },
      }),
    }),
  );
  await page.goto("/settings/org/audit");
  await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
  await expect(page.getByText("Events are kept for 90 days")).toBeVisible();
  await expect(page.getByTestId("audit-table").getByRole("row")).toHaveCount(3);

  await page.getByTestId("audit-type-filter").selectOption("tool_call");
  await expect(page.getByTestId("audit-table").getByRole("row")).toHaveCount(2);
  await expect(page.getByText("bash_exec")).toBeVisible();
});

test("an administrator configures a channel profile on a Slack bot", async ({ page }) => {
  let saved: Record<string, unknown> | null = null;
  await page.route("**/v1/admin/slack/bots", async (route) => {
    if (route.request().method() === "POST") {
      saved = route.request().postDataJSON();
      return route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "stop" }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await page.goto("/settings/slack");
  await page.getByTestId("new-bot-button").click();
  await page.getByTestId("bot-name-input").fill("Profiles Bot");
  await page.getByTestId("bot-token-input").fill("xoxb-dummy");
  await page.getByTestId("app-token-input").fill("xapp-dummy");

  await page.getByRole("tab", { name: "Routing" }).click();
  await page.getByTestId("routing-add-channel").click();
  await page.getByTestId("routing-match-1").fill("C0123ABCD");
  await page.getByTestId("routing-details-1").click();
  await page.getByLabel("Response mode").selectOption("private");
  await page.getByLabel("Allowed tools").fill("file_read, grep_search");
  await page.getByLabel("House rules").fill("Answer in French.");

  await page.getByTestId("save-bot-button").click();
  await expect.poll(() => saved).not.toBeNull();
  expect((saved as unknown as { policy: Record<string, unknown> }).policy).toEqual({
    channel_profiles: {
      C0123ABCD: { response_mode: "private", allowed_tools: ["file_read", "grep_search"], instructions: "Answer in French." },
    },
  });

  await page.getByRole("button", { name: "Remove C0123ABCD" }).click();
  await expect(page.getByTestId("routing-row-1")).toHaveCount(0);
});
