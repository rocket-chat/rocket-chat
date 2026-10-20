import { test, expect, type Page } from "@playwright/test";

/**
 * Locates the user's own prompt bubble in the flight stream.
 *
 * Scoping matters: a plain `getByText(prompt)` is ambiguous, because the agent
 * often echoes the prompt back (e.g. rendered as inline code), which trips
 * Playwright strict mode as soon as the response arrives.
 */
const userMessage = (target: Page, text: string) =>
  target.getByTestId("user-message").filter({ hasText: text });

test.describe("Session Lifecycle, Multi-User Sync & Navigation Integrity", () => {
  test.describe.configure({ mode: "serial" });

  test("switches sessions via sidebar without URL flicker or reversal", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/chat\/.+/);

    // Ignite first session
    const input = page.getByTestId("instruction-input");
    await input.fill("Session Alpha Turn");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    const firstUrl = page.url();

    // Create a second session
    const newMissionBtn = page.getByTestId("new-conversation-btn");
    await expect(newMissionBtn).toBeVisible();
    await newMissionBtn.click();
    await expect(page).toHaveURL("/chat/draft");

    // Ignite second session
    await input.fill("Session Beta Turn");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    const secondUrl = page.url();
    expect(firstUrl).not.toEqual(secondUrl);

    // Switch back to the first session by clicking its sidebar Link
    const firstPath = new URL(firstUrl).pathname;
    const firstSessionLink = page.locator(`a[href="${firstPath}"]`);
    await expect(firstSessionLink).toBeVisible();
    await firstSessionLink.click();

    // Verify the URL settled on firstUrl and did not flicker back to secondUrl.
    // Synchronise on the first session's *own transcript* instead of sleeping: the
    // flicker bug showed up as rendering the other session's content, so waiting for
    // this text to appear and then re-checking the URL catches it deterministically.
    await expect(page).toHaveURL(firstUrl);
    await expect(userMessage(page, "Session Alpha Turn")).toBeVisible();
    expect(new URL(page.url()).pathname).toEqual(firstPath);

    // Switch back to the second session
    const secondPath = new URL(secondUrl).pathname;
    const secondSessionLink = page.locator(`a[href="${secondPath}"]`);
    await expect(secondSessionLink).toBeVisible();
    await secondSessionLink.click();

    // Verify URL updates to secondUrl and remains stable
    await expect(page).toHaveURL(secondUrl);
    await expect(userMessage(page, "Session Beta Turn")).toBeVisible();
    expect(new URL(page.url()).pathname).toEqual(secondPath);

    // Test native browser history back and forward buttons
    await page.goBack();
    await expect(page).toHaveURL(firstUrl);

    await page.goForward();
    await expect(page).toHaveURL(secondUrl);
  });

  test("leaves a session and resumes with fully preserved conversation history", async ({ page }) => {
    await page.goto("/");

    // Create a dedicated session for this test
    const newMissionBtn = page.getByTestId("new-conversation-btn");
    await expect(newMissionBtn).toBeVisible();
    await newMissionBtn.click();
    await expect(page).toHaveURL("/chat/draft");

    // Submit a user prompt in this session
    const uniqueToken = `telemetry-verify-${Date.now()}`;
    const input = page.getByTestId("instruction-input");
    await expect(input).toBeEnabled();
    await input.fill(uniqueToken);
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    const sessionUrl = page.url();

    // Verify user prompt is visible in the chat log stream
    await expect(userMessage(page, uniqueToken)).toBeVisible();
    // The session must be registered server-side before we navigate away; waiting for
    // its sidebar entry is deterministic proof of that (this replaced a 1s sleep).
    const sessionPath = new URL(sessionUrl).pathname;
    await expect(page.locator(`a[href="${sessionPath}"]`)).toBeVisible();

    // Leave the session by navigating to the full-page settings suite
    await page.goto("/settings/agents");
    await expect(page).toHaveURL(/\/settings\/agents/);
    await expect(page.getByText("Custom agent personas")).toBeVisible();

    // Resume the session by direct URL deep-link
    await page.goto(sessionUrl);
    await expect(page).toHaveURL(sessionUrl);

    // Verify the previous message history was preserved and is visible in the chat stream
    await expect(userMessage(page, uniqueToken)).toBeVisible({ timeout: 10000 });
  });

  test("streams real-time updates across 2 concurrent users on the same session", async ({ browser, request }) => {
    // Create the shared session over REST, deliberately without a setup turn:
    // turn latency is model/sandbox bound (and can exceed a minute), so waiting
    // for one made this test flaky. UI-driven session creation is covered by the
    // lifecycle test above.
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: { title: "Collaborative Session" },
    });
    expect(createRes.ok()).toBeTruthy();
    const sharedSessionUrl = `/chat/${(await createRes.json()).session_id}`;

    // User 1 opens the session
    const context1 = await browser.newContext();
    const page1 = await context1.newPage();
    await page1.goto(sharedSessionUrl);
    await expect(page1).toHaveURL(sharedSessionUrl);

    // User 2 opens the exact same session URL in an independent context
    const context2 = await browser.newContext();
    const page2 = await context2.newPage();
    // The collaborator must be subscribed before the instruction is sent, or the test would
    // measure page-load speed (on Postgres it is slower) instead of the live broadcast.
    let collaboratorSynced = false;
    page2.on("websocket", (ws) => {
      if (/\/v1\/sessions\/.*\/ws/.test(ws.url())) ws.on("framereceived", () => (collaboratorSynced = true));
    });
    await page2.goto(sharedSessionUrl);
    await expect(page2).toHaveURL(sharedSessionUrl);
    await expect.poll(() => collaboratorSynced, { timeout: 15000 }).toBe(true);

    // A freshly created session is idle in both cockpits, so the Run control must
    // be present (the Queue/Stop pair only renders while a turn is in flight).
    await expect(page1.getByTestId("ignite-button")).toBeVisible();
    await expect(page2.getByTestId("ignite-button")).toBeVisible();

    // User 1 transmits an instruction
    const input1 = page1.getByTestId("instruction-input");
    const user1Prompt = `multiuser-sync-${Date.now()}`;
    await expect(input1).toBeEnabled();
    await input1.fill(user1Prompt);
    await page1.getByTestId("ignite-button").click();

    // User 1 sees their prompt and executing/processing state
    await expect(userMessage(page1, user1Prompt)).toBeVisible({ timeout: 15000 });
    await expect(page1.getByText(/Running\.\.\.|Executing in Sandbox|Reasoning|Executing|Synthesizing|Ready/i).first()).toBeVisible({ timeout: 10000 });

    // User 2 receives the *prompt itself* live over the WebSocket, without reloading:
    // the server broadcasts a USER_MESSAGE frame carrying the instruction.
    // This is the strong assertion the backend could not satisfy before the turn handler
    // published the event — asserting mere "executing" telemetry was a false positive.
    await expect(userMessage(page2, user1Prompt)).toBeVisible({ timeout: 15000 });

    // Rendered exactly once on the collaborator (a late joiner hydrating from the
    // snapshot must not get a second copy from the live broadcast).
    await expect(userMessage(page2, user1Prompt)).toHaveCount(1);

    // And exactly once on the originating client: its optimistic entry is deduped against
    // the echoed `message_id` rather than rendered twice.
    await expect(userMessage(page1, user1Prompt)).toHaveCount(1);

    // User 2 also receives the live telemetry state.
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
    const patchRes = await request.post(
      `http://localhost:8000/v1/dev/sessions/${sessionId}/transcript`,
      {
      data: {
        messages: [
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
      },
    );
    expect(patchRes.ok()).toBeTruthy();

    // 2. Navigate to this session in the web app
    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    // Verify ActionGroup reasoning block is rendered
    const actionGroupToggle = page.getByTestId("action-group-toggle").first();
    await expect(actionGroupToggle).toBeVisible({ timeout: 10000 });

    // Expand the collapsed-by-default reasoning block
    await actionGroupToggle.click();
    // Each step is one line; its detail (reasoning, command, output) opens on demand.
    const steps = page.getByTestId("activity-step");
    await expect(steps).toHaveCount(2);
    await expect(steps.nth(1)).toContainText("ls -la");
    await expect(steps.nth(0)).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("bash_exec", { exact: true })).toHaveCount(0);
    await steps.nth(0).click();
    await expect(steps.nth(0)).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText(testReasoning).first()).toBeVisible();
    await steps.nth(1).click();
    await expect(page.getByText("bash_exec", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(testOutput)).toBeVisible();

    // 3. Leave the chat by navigating to settings
    await page.goto("/settings/github");
    await expect(page).toHaveURL("/settings/github");
    await expect(page.getByText(/Git & version control/i).first()).toBeVisible();

    // 4. Return to the chat session
    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    // Verify ActionGroup is still present and preserved
    const restoredActionGroup = page.getByTestId("action-group-toggle").first();
    await expect(restoredActionGroup).toBeVisible({ timeout: 10000 });
    await restoredActionGroup.click();
    await page.getByTestId("activity-step").nth(0).click();
    await expect(page.getByText(testReasoning).first()).toBeVisible();
    await page.getByTestId("activity-step").nth(1).click();
    await expect(page.getByText("bash_exec", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Here is the summary of the analyzed workspace files.")).toBeVisible();
  });

  test("groups intermediate step prefaces into ActionGroup without emitting orphan answer bubbles", async ({
    page,
    request,
  }) => {
    const createRes = await request.post("http://localhost:8000/v1/sessions", {
      data: {
        title: "Step Preface Grouping Mission",
      },
    });
    expect(createRes.ok()).toBeTruthy();
    const sessionData = await createRes.json();
    const sessionId = sessionData.session_id;

    const prefaceText = "Inspecting CI runners and git log before rebase";
    const toolOutput = "commit 7073dfda";
    const finalText = "Successfully completed the rebase and verified all tests.";

    // Seed session with an intermediate preface message before tool execution, followed by the final answer
    const patchRes = await request.post(
      `http://localhost:8000/v1/dev/sessions/${sessionId}/transcript`,
      {
        data: {
          messages: [
            { role: "user", content: "Please rebase PR #3810" },
            { role: "assistant", content: prefaceText },
            {
              role: "assistant",
              content: "Executing tool: bash_exec",
              tool_calls: [
                {
                  id: "call-1",
                  name: "bash_exec",
                  arguments: { command: "git log -n 1" },
                  result: toolOutput,
                  status: "completed",
                },
              ],
            },
            { role: "assistant", content: finalText },
          ],
        },
      },
    );
    expect(patchRes.ok()).toBeTruthy();

    await page.goto(`/chat/${sessionId}`);
    await expect(page).toHaveURL(`/chat/${sessionId}`);

    // Verify ActionGroup is rendered and contains both the step intent and the tool call
    const actionGroupToggle = page.getByTestId("action-group-toggle").first();
    await expect(actionGroupToggle).toBeVisible({ timeout: 10000 });
    await actionGroupToggle.click();

    // Verify the step intent is rendered inside the ActionGroup
    const steps = page.getByTestId("activity-step");
    await expect(steps).toHaveCount(2);
    await expect(page.getByText("Step intent")).toBeVisible();
    await expect(steps.nth(0)).toContainText("Inspecting CI runners");
    // The kind and the start of the text are separate lines, so they never read as one sentence.
    const preview = steps.nth(0).getByTestId("step-preview");
    await expect(preview).toContainText("Inspecting CI runners");
    const titleBox = await steps.nth(0).getByText("Step intent").boundingBox();
    const previewBox = await preview.boundingBox();
    expect(previewBox!.y).toBeGreaterThan(titleBox!.y);

    // Click the step intent to view its markdown content
    await steps.nth(0).click();
    await expect(page.getByText(prefaceText).first()).toBeVisible();

    // Verify only the final answer is rendered as an assistant answer bubble, not the preface
    const answerBubbles = page.locator(".group.my-4.min-w-0");
    await expect(answerBubbles).toHaveCount(1);
    await expect(answerBubbles.first()).toContainText(finalText);
    await expect(answerBubbles.first()).not.toContainText(prefaceText);
  });

  test("an admin sees other members' sessions only on request, and CLEAR deletes only their own", async ({ page, request }) => {
    const mine = `Mission mine ${Date.now()}`;
    const theirs = `Mission of a teammate ${Date.now()}`;
    await request.post("http://localhost:8000/v1/sessions", { data: { title: mine } });
    const teammate = await request.post("http://localhost:8000/v1/sessions", {
      headers: { "X-Tenant-User-Id": "teammate_scope", "X-User-Role": "developer" },
      data: { title: theirs },
    });
    expect(teammate.ok()).toBeTruthy();

    await page.goto("/");
    await expect(page).toHaveURL(/\/chat\/.+/);
    const initialSessionUrl = page.url();
    const sidebar = page.getByTestId("session-sidebar");
    await expect(sidebar.getByText(mine)).toBeVisible({ timeout: 10000 });
    await expect(sidebar.getByText(theirs)).toHaveCount(0);

    const everyone = page.getByTestId("toggle-org-sessions-btn");
    await everyone.click();
    await expect(everyone).toHaveAttribute("aria-pressed", "true");
    await expect(sidebar.getByText(theirs)).toBeVisible({ timeout: 10000 });

    page.on("dialog", (dialog) => dialog.accept());
    await page.getByTestId("clear-all-sessions-btn").click();

    await expect(page).not.toHaveURL(initialSessionUrl, { timeout: 10000 });
    await expect(page).toHaveURL(/\/chat\/.+/);
    await expect(sidebar.getByText(mine)).toHaveCount(0, { timeout: 10000 });
    await expect(sidebar.getByText(theirs)).toBeVisible();
    await expect(page.locator(".continuous-flight-stream")).toBeVisible();

    await everyone.click();
    await expect(everyone).toHaveAttribute("aria-pressed", "false");
    await expect(sidebar.getByText(theirs)).toHaveCount(0, { timeout: 10000 });
  });
});
