import { test, expect, type Page } from "@playwright/test";
import { routeSessionSocket, type SessionSocket } from "./support/session-socket";

async function openSession(page: Page, request: import("@playwright/test").APIRequestContext) {
  const created = await request.post("http://localhost:8000/v1/sessions", { data: { title: "PR mission" } });
  const sessionId: string = (await created.json()).session_id;
  const socket = await routeSessionSocket(page, sessionId);
  await page.goto(`/chat/${sessionId}`);
  await socket.ready;
  return socket;
}

function openPullRequest(socket: SessionSocket, id: string, repo: string, number: number) {
  const url = `https://github.com/${repo}/pull/${number}`;
  socket.emit("tool_started", { id, name: "git_open_pr", arguments: { title: `PR ${number}` } });
  socket.emit("tool_completed", {
    id,
    name: "git_open_pr",
    arguments: { title: `PR ${number}` },
    result: `Opened ${url}`,
    status: "completed",
    duration_ms: 400,
  });
}

function editFile(socket: SessionSocket, id: string) {
  socket.emit("tool_started", { id, name: "file_edit", arguments: { path: "a.py" } });
  socket.emit("tool_completed", { id, name: "file_edit", arguments: { path: "a.py" }, result: "ok", status: "completed" });
}

function endTurn(socket: SessionSocket) {
  socket.emit("status_changed", { status: "completed", final: true });
}

test("a turn that opened two pull requests names each one", async ({ page, request }) => {
  const socket = await openSession(page, request);
  socket.emit("status_changed", { status: "running" });
  editFile(socket, "e1");
  openPullRequest(socket, "p1", "acme/api", 7);
  openPullRequest(socket, "p2", "acme/web", 12);
  endTurn(socket);

  const links = page.getByTestId("turn-summary").getByTestId("turn-summary-pr");
  await expect(links).toHaveCount(2, { timeout: 10000 });
  await expect(links.nth(0)).toHaveText("acme/api#7");
  await expect(links.nth(0)).toHaveAttribute("href", "https://github.com/acme/api/pull/7");
  await expect(links.nth(1)).toHaveText("acme/web#12");
  await expect(page.getByTestId("turn-summary-earlier-prs")).toHaveCount(0);
});

test("the pull requests of earlier turns stay reachable without crowding the new one", async ({ page, request }) => {
  const socket = await openSession(page, request);
  socket.emit("status_changed", { status: "running" });
  editFile(socket, "e1");
  openPullRequest(socket, "p1", "acme/api", 7);
  endTurn(socket);
  await expect(page.getByTestId("turn-summary-pr")).toHaveCount(1, { timeout: 10000 });

  socket.emit("user_message", { message_id: "m2", content: "Now the web app" });
  socket.emit("status_changed", { status: "running" });
  editFile(socket, "e2");
  openPullRequest(socket, "p2", "acme/web", 12);
  endTurn(socket);

  const summary = page.getByTestId("turn-summary");
  await expect(summary.getByTestId("turn-summary-pr").first()).toHaveText("acme/web#12", { timeout: 10000 });
  const earlier = page.getByTestId("turn-summary-earlier-prs");
  await expect(earlier).toContainText("1 other pull request in this session");
  await earlier.locator("summary").click();
  await expect(earlier.getByTestId("turn-summary-pr")).toHaveText("acme/api#7");
});

test("a wait on a pull request opens the address the session already used", async ({ page, request }) => {
  const socket = await openSession(page, request);
  socket.emit("status_changed", { status: "running" });
  socket.emit("tool_started", { id: "p1", name: "git_open_pr", arguments: {} });
  socket.emit("tool_completed", {
    id: "p1",
    name: "git_open_pr",
    arguments: {},
    result: "Opened https://ghe.example.com/acme/api/pull/9",
    status: "completed",
  });
  socket.emit("wait_started", { wait_id: "w9", wait_type: "ci_status", target: "acme/api#9", timeout_seconds: 600 });

  await expect(page.getByTestId("wait-open-pr")).toHaveAttribute("href", "https://ghe.example.com/acme/api/pull/9", {
    timeout: 10000,
  });
});
