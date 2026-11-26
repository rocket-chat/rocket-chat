import { test, expect } from "@playwright/test";
import { API_URL } from "./support/env";

/**
 * An agent with "Propose changes" stages an edit of its instructions; the change waits under
 * Settings > Proposals and only an approval (here, edited by the reviewer) saves it.
 */
test("an agent's proposal waits for review and an edited approval saves the reviewer's text", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const agentName = `Data assistant ${suffix}`;
  const created = await request.post(`${API_URL}/v1/agents`, {
    data: {
      id: `proposals-${suffix}`,
      name: agentName,
      role_title: "Analyst",
      system_prompt: "You answer data questions.\nAlways answer in English.",
      model: "",
      proposes_changes: true,
      scope: "org",
    },
  });
  expect(created.ok()).toBeTruthy();
  const agent = await created.json();

  try {
    const seeded = await request.post(`${API_URL}/v1/dev/agents/${agent.id}/proposals`, {
      data: {
        old_text: "Always answer in English.",
        new_text: "Answer in the language you are asked in.",
        summary: `Answer in the asker's language ${suffix}`,
        rationale: "The operator asked for it in the review channel.",
      },
    });
    expect((await seeded.json()).outcome).toContain("waiting for review");

    await page.goto("/settings/proposals");
    await page.getByRole("button", { name: new RegExp(`asker's language ${suffix}`) }).click();
    const detail = page.getByTestId("proposal-detail");
    await expect(detail).toContainText("The operator asked for it");
    await expect(page.getByTestId("proposal-text")).toHaveValue(/language you are asked in/);

    await page.getByTestId("proposal-text").fill("You answer data questions, in the asker's language.");
    await page.getByTestId("proposal-note").fill("Shorter.");
    await page.getByTestId("proposal-approve").click();
    await expect(detail).toBeHidden();

    const agents = await (await request.get(`${API_URL}/v1/agents`)).json();
    const saved = agents.find((a: { id: string }) => a.id === agent.id);
    expect(saved.system_prompt).toBe("You answer data questions, in the asker's language.");

    await page.getByTestId("proposals-status").selectOption("approved");
    await expect(page.getByTestId("proposals-list")).toContainText(`asker's language ${suffix}`);
  } finally {
    await request.delete(`${API_URL}/v1/agents/${agent.id}`);
  }
});

test("the agent editor saves the sandbox, session-reading and proposal switches", async ({ page, request }) => {
  const agentName = `Switches ${Date.now()}`;
  try {
    await page.goto("/settings/agents");
    await page.getByTestId("agent-new").click();
    await page.getByTestId("agent-name").fill(agentName);
    await page.getByTestId("agent-section-sandbox").click();
    await expect(page.getByTestId("agent-sandbox")).toBeVisible();
    await page.getByTestId("agent-sandbox-enabled").uncheck();
    await expect(page.getByTestId("agent-sandbox")).toBeHidden();
    await page.getByTestId("agent-reads-sessions").check();
    await page.getByTestId("agent-proposes-changes").check();
    await page.getByTestId("agent-save").click();
    await expect(page.getByTestId("agent-save")).toHaveText(/Saved/);

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === agentName);
    expect(saved.sandbox_enabled).toBe(false);
    expect(saved.reads_agent_sessions).toBe(true);
    expect(saved.proposes_changes).toBe(true);
  } finally {
    const agents = await (await request.get(`${API_URL}/v1/agents`)).json();
    for (const agent of agents) {
      if (agent.name === agentName) await request.delete(`${API_URL}/v1/agents/${agent.id}`);
    }
  }
});
