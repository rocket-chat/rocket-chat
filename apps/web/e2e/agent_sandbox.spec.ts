import { test, expect } from "@playwright/test";

const API = "http://localhost:8000";

/**
 * An agent names the sandbox type its sessions start in; the composer's sandbox picker shows
 * it as the default, and a sandbox picked there still wins.
 */
test("an agent's sandbox type is saved and offered as the composer's default", async ({ page, request }) => {
  const suffix = Date.now();
  const boxName = `High CPU ${suffix}`;
  const agentName = `Backend builder ${suffix}`;
  const created = await request.post(`${API}/v1/admin/sandboxes`, {
    data: { name: boxName, image: `registry.example.com/team/high-cpu:${suffix}` },
  });
  expect(created.ok()).toBeTruthy();
  const box = await created.json();

  try {
    await page.goto("/settings/agents");
    await page.getByRole("button", { name: /New agent/i }).first().click();
    await page.locator("input").first().fill(agentName);
    const sandbox = page.getByTestId("agent-sandbox");
    await expect(sandbox.locator("option").first()).toHaveText("Organization default");
    await sandbox.selectOption({ label: boxName });
    await page.getByRole("button", { name: /save persona/i }).click();
    await expect(page.getByText("SAVED!")).toBeVisible();

    const agents = await (await page.request.get("/v1/agents")).json();
    const saved = agents.find((a: { name: string }) => a.name === agentName);
    expect(saved.sandbox_id).toBe(box.id);

    await page.goto("/");
    await page.getByTestId("agent-model-picker").click();
    await page.getByRole("button", { name: new RegExp(agentName) }).first().click();
    await expect(page.getByTestId("sandbox-picker").locator("option").first()).toHaveText(
      `Agent's: ${boxName}`,
    );
  } finally {
    const agents = await (await request.get(`${API}/v1/agents`)).json();
    for (const agent of agents) {
      if (agent.name === agentName) await request.delete(`${API}/v1/agents/${agent.id}`);
    }
    await request.delete(`${API}/v1/admin/sandboxes/${box.id}`);
  }
});
