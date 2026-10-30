import { test, expect } from "@playwright/test";
import { routeSessionSocket } from "./support/session-socket";

const API = "http://localhost:8000";

async function openSession(page: import("@playwright/test").Page, request: import("@playwright/test").APIRequestContext, title: string) {
  const created = await request.post(`${API}/v1/sessions`, { data: { title } });
  const sessionId: string = (await created.json()).session_id;
  await request.post(`${API}/v1/dev/sessions/${sessionId}/transcript`, {
    data: {
      messages: [
        { role: "user", content: "First question" },
        { role: "assistant", content: "First answer" },
        { role: "user", content: "Second question" },
        { role: "assistant", content: "Second answer" },
      ],
    },
  });
  const socket = await routeSessionSocket(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await socket.ready;
  return { sessionId, socket };
}

/** A subagent's own steps nest under its card, and its structured report is shown there. */
test("a subagent card lists its steps and its report", async ({ page, request }) => {
  const { socket } = await openSession(page, request, "Subagent");
  socket.emit("subagent_started", { subagent_id: "sub_1", role: "reviewer", task: "Review the diff", parent_tool_call_id: "c1", model: "m" });
  socket.emit("tool_started", { id: "s1", name: "grep_search", arguments: { pattern: "x" }, subagent_id: "sub_1", parent_tool_call_id: "c1" });
  socket.emit("tool_completed", { id: "s1", name: "grep_search", arguments: {}, result: "a.py:1", status: "completed", duration_ms: 20, subagent_id: "sub_1", parent_tool_call_id: "c1" });
  socket.emit("subagent_completed", {
    subagent_id: "sub_1",
    role: "reviewer",
    task: "Review the diff",
    summary: "One bug",
    status: "partial",
    report: { status: "partial", summary: "One bug", findings: [{ file: "a.py", line: 1, severity: "high", evidence: "crash" }], changes: null, open_questions: [], next_steps: [], data: null },
  });

  await expect(page.getByTestId("flight-log-stream")).not.toContainText("Executing tool: grep_search");
  await page.getByRole("button", { name: /Delegated to 1 specialist/ }).click();
  await page.getByTestId("activity-step").and(page.getByTitle("Delegated to reviewer: Review the diff")).click();
  await expect(page.getByTestId("subagent-steps")).toContainText("grep_search");
  await expect(page.getByTestId("subagent-status")).toHaveText("Partial");
  await expect(page.getByText("crash")).toBeVisible();
});

/** A question is answered with radios (one choice) or checkboxes (several), and Other. */
test("a multi-select question takes several options and a free answer", async ({ page, request }) => {
  const { socket } = await openSession(page, request, "Question");
  socket.emit("decision_required", { question_id: "q1", question_text: "Which files?", options: ["a.py", "b.py"], is_multi_select: true });
  const card = page.getByTestId("decision-card");
  await card.getByTestId("decision-option-1").check();
  await card.getByTestId("decision-option-2").check();
  await card.getByTestId("decision-other").fill("and c.py");
  await card.getByTestId("decision-submit").click();
  await expect
    .poll(() => socket.sent.find((f) => (f as { type?: string }).type === "answer_question"))
    .toMatchObject({ selected_options: ["a.py", "b.py"], custom_text: "and c.py" });
});

/** An approval can be remembered for the session. */
test("an approval can be allowed for the whole session", async ({ page, request }) => {
  const { socket } = await openSession(page, request, "Approval");
  socket.emit("approval_required", {
    action_id: "ap_1",
    action_type: "bash_exec",
    command_or_path: "bash_exec: npm test",
    risk_level: "medium",
    tool: "bash_exec",
    arguments: { command: "npm test" },
    remember_options: ["session", "repository"],
  });
  await page.getByTestId("approval-remember-session").click();
  await expect
    .poll(() => socket.sent.find((f) => (f as { type?: string }).type === "approval"))
    .toMatchObject({ action_id: "ap_1", approved: true, remember: "session" });
});

/** A proposed plan is approved in a chosen mode. */
test("a plan card approves the plan in the chosen mode", async ({ page, request }) => {
  const { sessionId, socket } = await openSession(page, request, "Plan");
  let decided: unknown = null;
  await page.route(`**/v1/sessions/${sessionId}/plan/decision`, async (route) => {
    decided = route.request().postDataJSON();
    await route.fulfill({ json: { action_id: "plan_1", approved: true, permission_mode: "auto", edited: false } });
  });
  socket.emit("approval_required", { action_id: "plan_1", action_type: "execute_plan", command_or_path: "1. Do it", risk_level: "medium" });
  const card = page.getByTestId("plan-card");
  await card.getByTestId("plan-mode").selectOption("auto");
  await card.getByTestId("plan-approve").click();
  await expect.poll(() => decided).toMatchObject({ action_id: "plan_1", approved: true, permission_mode: "auto" });
  await expect(card).toHaveCount(0);
});

/** An answer can be rated, with what was wrong. */
test("an answer can be rated down with a comment", async ({ page, request }) => {
  const { sessionId } = await openSession(page, request, "Rating");
  const answer = page.getByText("Second answer");
  await answer.hover();
  await page.getByTestId("answer-thumbs-down").last().click();
  await page.getByTestId("answer-feedback-form").getByRole("textbox").fill("Wrong file");
  await page.getByTestId("answer-feedback-form").getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Thanks, noted." })).toBeVisible();
  const mine = await (await request.get(`${API}/v1/sessions/${sessionId}/feedback`)).json();
  expect(mine).toEqual([expect.objectContaining({ turn_number: 2, rating: "down", comment: "Wrong file" })]);
});

/** The composer runs its own commands from the keyboard. */
test("the slash menu runs a built-in command with the keyboard", async ({ page, request }) => {
  await openSession(page, request, "Commands");
  const input = page.getByTestId("instruction-input");
  await input.fill("/pl");
  await expect(page.getByTestId("slash-menu")).toContainText("/plan");
  await input.press("Enter");
  await expect(page.getByTestId("command-notice")).toContainText("Plan mode");
  await expect(input).toHaveValue("");
});

/** The command palette opens with ⌘K and shows the Git tab. */
test("the command palette opens the Git tab", async ({ page, request }) => {
  await openSession(page, request, "Palette");
  await page.keyboard.press("ControlOrMeta+k");
  await page.getByTestId("command-palette-input").fill("branch and pull");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("tab-git")).toHaveAttribute("aria-selected", "true");
});
