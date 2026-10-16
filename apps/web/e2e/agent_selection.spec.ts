import { test, expect, type Page } from "@playwright/test";

const API = "http://localhost:8000";

/** The model catalogue as the API returns it, far more than the placeholder list. */
const CATALOGUE = {
  default_model: "openrouter/deepseek/deepseek-v4.1-flash",
  models: Array.from({ length: 240 }, (_, i) => ({
    id: `openrouter/vendor-${i % 12}/model-${i}`,
    name: `Vendor ${i % 12} Model ${i}`,
    context_window: 131072,
    provider: `Vendor ${i % 12}`,
  })).concat([
    {
      id: "openrouter/acme/thinker-pro",
      name: "Acme Thinker Pro",
      context_window: 200000,
      provider: "Acme",
    },
  ]),
};

async function serveCatalogue(page: Page) {
  await page.route("**/v1/models", (route) => route.fulfill({ json: CATALOGUE }));
}

test("the agent editor searches the full model catalogue", async ({ page }) => {
  await serveCatalogue(page);
  await page.goto("/settings/agents");
  await page.getByRole("button", { name: /New agent/i }).first().click();

  const picker = page.getByTestId("agent-model");
  await expect(picker).toContainText("Organization default");
  await picker.click();
  const search = page.getByTestId("agent-model-search");
  await expect(search).toHaveAttribute("placeholder", /Search 241 models/);
  await search.fill("acme thinker");
  await page.getByRole("option", { name: /Acme Thinker Pro/ }).click();

  await expect(picker).toContainText("Acme Thinker Pro");
  await expect(picker).toContainText("openrouter/acme/thinker-pro");
});

test("an answer keeps the agent that wrote it when the selection changes", async ({ page, request }) => {
  const created = await request.post(`${API}/v1/sessions`, { data: { title: "Who answered" } });
  const sessionId = (await created.json()).session_id as string;
  const sent: Record<string, unknown>[] = [];
  await page.routeWebSocket(/\/ws/, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.type === "turn") sent.push(frame);
      server.send(message);
    });
    server.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.session?.session_id === sessionId) {
        frame.session.conversation_history = [
          {
            role: "user",
            content: "Review this",
            agent_id: "persona-security",
            agent_name: "Security & Penetration Auditor",
            model: "openrouter/acme/thinker-pro",
          },
          { role: "assistant", content: "Reviewed: no findings." },
        ];
      }
      ws.send(JSON.stringify(frame));
    });
  });

  await serveCatalogue(page);
  await page.goto(`/chat/${sessionId}`);
  const answeredBy = page.getByTestId("answered-by").first();
  await expect(answeredBy).toHaveText("Security & Penetration Auditor");

  // A raw model for the next message: the answer above still names its own agent.
  await page.getByTestId("agent-model-picker").click();
  await page.getByPlaceholder(/Filter models/).fill("thinker");
  await page.getByRole("button", { name: /Acme Thinker Pro/ }).click();
  await expect(answeredBy).toHaveText("Security & Penetration Auditor");

  await page.getByTestId("instruction-input").fill("Now fix it");
  await page.getByTestId("ignite-button").click();
  await expect.poll(() => sent.length).toBeGreaterThan(0);
  expect(sent[0]).toMatchObject({ mode: "direct", model: "openrouter/acme/thinker-pro" });
  expect(sent[0]).not.toHaveProperty("agent_id");
});
