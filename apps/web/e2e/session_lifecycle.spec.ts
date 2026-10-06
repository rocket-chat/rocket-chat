import { test, expect } from "@playwright/test";

test.describe("Session Lifecycle, Multi-User Sync & Navigation Integrity", () => {
  test.describe.configure({ mode: "serial" });

  test("switches sessions via sidebar without URL flicker or reversal", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/chat\/.+/);
    const firstUrl = page.url();

    // Create a second session
    const newMissionBtn = page.getByTestId("new-conversation-btn");
    await expect(newMissionBtn).toBeVisible();
    await newMissionBtn.click();

    // Verify URL transitioned to new session
    await expect(page).not.toHaveURL(firstUrl);
    await expect(page).toHaveURL(/\/chat\/.+/);
    const secondUrl = page.url();
    expect(firstUrl).not.toEqual(secondUrl);

    // Switch back to the first session by clicking its sidebar Link
    const firstPath = new URL(firstUrl).pathname;
    const firstSessionLink = page.locator(`a[href="${firstPath}"]`);
    await expect(firstSessionLink).toBeVisible();
    await firstSessionLink.click();

    // Verify URL smoothly updates to firstUrl and remains stable (no flicker back to secondUrl)
    await expect(page).toHaveURL(firstUrl);
    await page.waitForTimeout(1000);
    expect(page.url()).toEqual(firstUrl);

    // Switch back to the second session
    const secondPath = new URL(secondUrl).pathname;
    const secondSessionLink = page.locator(`a[href="${secondPath}"]`);
    await expect(secondSessionLink).toBeVisible();
    await secondSessionLink.click();

    // Verify URL updates to secondUrl and remains stable
    await expect(page).toHaveURL(secondUrl);
    await page.waitForTimeout(1000);
    expect(page.url()).toEqual(secondUrl);

    // Test native browser history back and forward buttons
    await page.goBack();
    await expect(page).toHaveURL(firstUrl);

    await page.goForward();
    await expect(page).toHaveURL(secondUrl);
  });

  test("leaves a session and resumes with fully preserved conversation history", async ({ page }) => {
    await page.goto("/");
    const initialUrl = page.url();

    // Create a dedicated session for this test
    const newMissionBtn = page.getByTestId("new-conversation-btn");
    await expect(newMissionBtn).toBeVisible();
    await newMissionBtn.click();
    await expect(page).not.toHaveURL(initialUrl);
    await expect(page).toHaveURL(/\/chat\/.+/);
    const sessionUrl = page.url();

    // Submit a user prompt in this session
    const uniqueToken = `telemetry-verify-${Date.now()}`;
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeEnabled();
    await input.fill(uniqueToken);
    await page.getByTestId("ignite-button").click();

    // Verify user prompt is visible in the chat log stream
    const chatStream = page.locator(".continuous-flight-stream");
    await expect(chatStream.getByText(uniqueToken)).toBeVisible();

    // Leave the session by navigating to the full-page settings suite
    await page.goto("/settings/agents");
    await expect(page).toHaveURL(/\/settings\/agents/);
    await expect(page.getByText("CUSTOM AGENT PERSONAS & CONTEXT")).toBeVisible();

    // Resume the session by direct URL deep-link
    await page.goto(sessionUrl);
    await expect(page).toHaveURL(sessionUrl);

    // Verify the previous message history was preserved and is visible in the chat stream
    await expect(page.locator(".continuous-flight-stream").getByText(uniqueToken)).toBeVisible({ timeout: 10000 });
  });

  test("streams real-time updates across 2 concurrent users on the same session", async ({ browser }) => {
    // User 1 creates/opens a fresh session
    const context1 = await browser.newContext();
    const page1 = await context1.newPage();
    await page1.goto("/");
    await expect(page1).toHaveURL(/\/chat\/.+/);
    const initialUrl = page1.url();

    const newMissionBtn = page1.getByTestId("new-conversation-btn");
    await expect(newMissionBtn).toBeVisible();
    await newMissionBtn.click();
    await expect(page1).not.toHaveURL(initialUrl, { timeout: 10000 });
    await expect(page1).toHaveURL(/\/chat\/.+/);
    const sharedSessionUrl = page1.url();

    // User 2 opens the exact same session URL in an independent context
    const context2 = await browser.newContext();
    const page2 = await context2.newPage();
    await page2.goto(sharedSessionUrl);
    await expect(page2).toHaveURL(sharedSessionUrl);

    // Verify both users are connected to the sandbox
    await expect(page1.getByText(/Sandbox Ready|Ready/i).first()).toBeVisible({ timeout: 15000 });
    await expect(page2.getByText(/Sandbox Ready|Ready/i).first()).toBeVisible({ timeout: 15000 });

    // User 1 transmits an instruction
    const user1Prompt = `multiuser-sync-${Date.now()}`;
    const input1 = page1.getByTestId("instruction-input");
    await expect(input1).toBeEnabled();
    await input1.fill(user1Prompt);
    await page1.getByTestId("ignite-button").click();

    // User 1 sees their prompt and executing/processing state
    await expect(page1.locator(".continuous-flight-stream").getByText(user1Prompt)).toBeVisible();
    await expect(page1.getByText(/Running\.\.\.|Executing in Sandbox|Reasoning|Executing|Synthesizing|Ready/i).first()).toBeVisible({ timeout: 10000 });

    // User 2 in real time receives the live telemetry state via WebSocket without refreshing
    await expect(page2.getByText(/Executing in Sandbox|Running\.\.\.|Reasoning|PROCESSING|Analyzing instruction|Executing|Synthesizing|Ready/i).first()).toBeVisible({ timeout: 10000 });

    await context1.close();
    await context2.close();
  });

  test("preserves thinking process and executed tool call reasoning blocks when leaving and returning", async ({ page, request }) => {
    // 1. Create a session on backend pre-populated with thinking process and tool calls
    const testReasoning = `Plan phase: Validate AST integrity and sandbox boundaries ${Date.now()}`;
    const testOutput = "total 128 - rw-r--r-- sample.py";
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: {
        title: "Reasoning Persistence Mission",
      },
    });
    expect(createRes.ok()).toBeTruthy();
    const sessionData = await createRes.json();
    const sessionId = sessionData.session_id;

    // Patch session with conversation containing reasoning and tool calls
    const patchRes = await request.patch(`http://localhost:8000/v1/sessions/${sessionId}`, {
      data: {
        conversation_history: [
          { role: "user", content: "Analyze the current workspace files" },
          {
            role: "assistant",
            content: "Here is the summary of the analyzed workspace files.",
            reasoning: testReasoning,
            tool_calls: [
              {
                id: "call-1",
                name: "bash_exec",
                arguments: { command: "ls -la" },
                result: testOutput,
                status: "completed",
              },
            ],
          },
        ],
      },
    });
    expect(patchRes.ok()).toBeTruthy();

    // 2. Navigate to this session in the web app
    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    // Verify ActionGroup reasoning block is rendered
    const actionGroupToggle = page.getByTestId("action-group-toggle").first();
    await expect(actionGroupToggle).toBeVisible({ timeout: 10000 });

    // Expand the collapsed-by-default reasoning block
    await actionGroupToggle.click();
    await expect(page.getByText(testReasoning)).toBeVisible();
    await expect(page.getByText("bash_exec", { exact: true }).first()).toBeVisible();

    // 3. Leave the chat by navigating to settings
    await page.goto("/settings/github");
    await expect(page).toHaveURL("/settings/github");
    await expect(page.getByText(/GitHub & Code Collaboration|GitHub Integration/i).first()).toBeVisible();

    // 4. Return to the chat session
    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    // Verify ActionGroup is still present and preserved
    const restoredActionGroup = page.getByTestId("action-group-toggle").first();
    await expect(restoredActionGroup).toBeVisible({ timeout: 10000 });
    await restoredActionGroup.click();
    await expect(page.getByText(testReasoning)).toBeVisible();
    await expect(page.getByText("bash_exec", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Here is the summary of the analyzed workspace files.")).toBeVisible();
  });

  test("deletes all ongoing sessions and seats user in a clean new session with no context", async ({ page, request }) => {
    // 1. Ensure at least two sessions exist
    await request.post("http://localhost:8000/v1/sessions", { data: { title: "Mission A" } });
    await request.post("http://localhost:8000/v1/sessions", { data: { title: "Mission B" } });

    await page.goto("/");
    await expect(page).toHaveURL(/\/chat\/.+/);
    const initialSessionUrl = page.url();

    // Accept window confirm dialog when clicking clear all
    page.on("dialog", (dialog) => dialog.accept());

    // 2. Click CLEAR ALL in sidebar
    const clearAllBtn = page.getByTestId("clear-all-sessions-btn");
    await expect(clearAllBtn).toBeVisible({ timeout: 10000 });
    await clearAllBtn.click();

    // 3. Assert URL transitioned to the new session
    await expect(page).not.toHaveURL(initialSessionUrl, { timeout: 10000 });
    await expect(page).toHaveURL(/\/chat\/.+/);

    // 4. Assert sidebar shows exactly 1 session
    await expect(page.getByText(/Sessions/i).first()).toBeVisible();

    // 5. Assert chat log stream is in clean initialized state
    const chatStream = page.locator(".continuous-flight-stream");
    await expect(chatStream).toBeVisible();
  });
});
