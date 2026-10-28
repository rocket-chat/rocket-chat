import { test, expect, type WebSocketRoute } from "@playwright/test";

const BACKEND = "http://localhost:8000";

test("an artifact the agent saved is a card in the conversation that opens it, live or reloaded", async ({ page }) => {
  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Deliverables" } });
  const sessionId = (await created.json()).session_id as string;
  // The turn as the history keeps it: the call that saved the artifact, with the tool's answer.
  await page.request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, {
    data: {
      messages: [
        { role: "user", content: "Write the migration plan" },
        {
          role: "assistant",
          content: "The plan is saved.",
          tool_calls: [
            {
              id: "call_1",
              name: "create_artifact",
              arguments: { title: "Migration Plan", filename: "migration_plan.md", content: "# Plan" },
              result:
                "Artifact 'Migration Plan' created successfully.\n- Artifact ID: art_plan1\n- Filename: migration_plan.md\n- Type: markdown (version 1)",
              status: "success",
            },
          ],
        },
      ],
    },
  });
  // The list the inspector reads: the artifact as stored.
  await page.route(`**/v1/sessions/${sessionId}/artifacts`, (route) =>
    route.fulfill({
      json: [
        {
          id: "art_plan1",
          session_id: sessionId,
          title: "Migration Plan",
          filename: "migration_plan.md",
          artifact_type: "markdown",
          version: 1,
          created_at: 1,
          updated_at: 1,
        },
      ],
    }),
  );
  await page.route(`**/v1/sessions/${sessionId}/artifacts/art_plan1`, (route) =>
    route.fulfill({ json: { id: "art_plan1", content: "# Plan\n\nMove the tables first." } }),
  );
  await page.route(`**/v1/sessions/${sessionId}/artifacts/art_plan1/versions`, (route) => route.fulfill({ json: [] }));

  await page.goto(`/chat/${sessionId}`);
  const card = page.getByTestId("artifact-card");
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("Migration Plan");
  await expect(card).toContainText("migration_plan.md · Created · v1");

  await card.click();
  await expect(page.getByText("Move the tables first.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Migration Plan" })).toBeVisible();
});

test("a card being saved settles in place, without a second entry for the same artifact", async ({ page }) => {
  const created = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title: "Live deliverable" } });
  const sessionId = (await created.json()).session_id as string;
  await page.request.post(`${BACKEND}/v1/dev/sessions/${sessionId}/transcript`, {
    data: { messages: [{ role: "user", content: "Draw the architecture" }] },
  });
  const record = await (await page.request.get(`${BACKEND}/v1/sessions/${sessionId}`)).json();
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/v1\/sessions\/[^/]+\/ws/, (ws) => {
    socket = ws;
    ws.send(JSON.stringify({ type: "sync", session: record, last_seq: 0 }));
  });

  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByTestId("flight-log-stream")).toContainText("Draw the architecture");
  await expect.poll(() => socket !== null).toBe(true);
  const send = (frame: object) => socket!.send(JSON.stringify({ session_id: sessionId, ...frame }));
  const args = { title: "Architecture", filename: "architecture.mermaid", content: "graph TD", artifact_type: "diagram" };

  send({ event_type: "tool_started", seq: 1, payload: { id: "call_9", name: "create_artifact", arguments: args } });
  const card = page.getByTestId("artifact-card");
  await expect(card).toContainText("Saving…");

  send({
    event_type: "artifact_created",
    seq: 2,
    payload: { id: "art_arch", title: "Architecture", filename: "architecture.mermaid", artifact_type: "diagram", version: 1, created_at: 1 },
  });
  send({
    event_type: "tool_completed",
    seq: 3,
    payload: {
      id: "call_9",
      name: "create_artifact",
      arguments: args,
      status: "success",
      result: "Artifact 'Architecture' created successfully.\n- Artifact ID: art_arch\n- Type: diagram (version 1)",
    },
  });

  await expect(card).toHaveCount(1);
  await expect(card).toContainText("architecture.mermaid · Created · v1");
  await expect(card).toHaveAttribute("data-artifact-id", "art_arch");
});
