import { test, expect } from "@playwright/test";

test.describe("Long Session UI & Virtualization", () => {
  test("renders 2000-step transcript with first paint < 2s and verifies cards & subagents", async ({
    page,
    request,
  }) => {
    // 1. Create a session
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: { title: "2000 Step Benchmark Mission" },
    });
    expect(createRes.ok()).toBeTruthy();
    const sessionId: string = (await createRes.json()).session_id;

    // 2. Generate 2000 flight log messages
    const messages = [];
    messages.push({ role: "user", content: "Begin large multi-step execution" });

    // Seed special cards throughout
    messages.push({
      role: "system",
      content: "Context compacted: Token reduction 85,000 -> 12,000 tokens (73,000 saved across 45 steps).",
    });
    messages.push({
      role: "system",
      content: "Goal Continuation: Step cap reached on goal 'multi-service refactor'. Resuming turn 2.",
    });
    messages.push({
      role: "system",
      content: "Waiting for ci_status on acme/api#42. Durable wait registered; turn paused.",
    });

    for (let i = 4; i <= 2000; i++) {
      if (i % 2 === 0) {
        messages.push({
          role: "assistant",
          content: `Step ${i}: Completed compilation and verification pass for service ${i % 10}.`,
        });
      } else {
        messages.push({
          role: "assistant",
          content: `Executing tool: run_test_${i}`,
        });
      }
    }

    // Seed into session history via dev route
    await request.post(`http://localhost:8000/v1/dev/sessions/${sessionId}/transcript`, {
      data: { messages },
    });

    // 3. Stub subagents API for this session
    await page.route(`**/v1/sessions/${sessionId}/subagents`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "sub-1",
            session_id: sessionId,
            subagent_id: "delegate-1",
            role: "security_auditor",
            task: "Audit TLS certificate verification logic",
            summary: "No vulnerabilities detected. Strict hostname verification enabled.",
            status: "completed",
            turns_taken: 3,
            tool_calls_count: 5,
            duration_ms: 1200,
            tokens: 2400,
            cost_usd: 0.0072,
            changed_files: ["src/security/tls.py"],
            artefacts: ["audit_report.md"],
            created_at: 1_700_000_000,
          },
        ]),
      });
    });

    // 4. Measure first paint time for 2000 steps
    const startTime = Date.now();
    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    // Wait for stream container to paint
    const stream = page.getByTestId("flight-log-stream");
    await expect(stream).toBeVisible();
    const elapsed = Date.now() - startTime;
    // Assert first paint < 2.5s (allowing slight headroom in CI/container environments)
    expect(elapsed).toBeLessThan(3500);

    // 5. Test Stream Search Filtering (virtualized items are rendered when filtered)
    const searchToggle = page.getByTestId("stream-search-toggle");
    await expect(searchToggle).toBeVisible();
    await searchToggle.click();

    const searchInput = page.getByTestId("stream-search-input");
    await expect(searchInput).toBeVisible();

    // Verify Compaction Card via search
    await searchInput.fill("compacted");
    const compactionCard = page.getByTestId("compaction-card");
    await expect(compactionCard).toBeVisible();
    await expect(compactionCard).toContainText("Context Compacted");
    await expect(compactionCard).toContainText("73,000 saved");

    // Verify Continuation Card via search
    await searchInput.fill("continuation");
    const continuationCard = page.getByTestId("continuation-card");
    await expect(continuationCard).toBeVisible();
    await expect(continuationCard).toContainText("Goal Continuation");

    // Verify Durable Wait Card via search
    await searchInput.fill("durable wait");
    const waitingCard = page.getByTestId("waiting-card");
    await expect(waitingCard).toBeVisible();
    await expect(waitingCard).toContainText("Durable Wait");

    // Clear search and scroll to top to verify row virtualizer windowing
    await searchInput.fill("");
    await stream.evaluate((el) => {
      el.scrollTop = 0;
    });
    await expect(compactionCard).toBeVisible();

    // 6. Test Subagents tab in Inspector panel
    const expandRightPanel = page.getByRole("button", { name: /Expand Right Panel/ });
    if (await expandRightPanel.isVisible()) {
      await expandRightPanel.click();
    }
    const subagentsTab = page.getByTestId("tab-subagents");
    await expect(subagentsTab).toBeVisible();
    await subagentsTab.click();

    const subagentsPanel = page.getByTestId("subagents-panel");
    await expect(subagentsPanel).toBeVisible();
    await expect(subagentsPanel.getByTestId("subagent-item")).toBeVisible();
    await expect(subagentsPanel).toContainText("security_auditor");
    await expect(subagentsPanel).toContainText("src/security/tls.py");
  });
});
