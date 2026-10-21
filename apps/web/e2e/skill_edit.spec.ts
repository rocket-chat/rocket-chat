import { test, expect, type Page } from "@playwright/test";

const API = "http://localhost:8000";

async function createSkills(page: Page, prefix: string, count: number): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < count; i++) {
    const res = await page.request.post(`${API}/v1/skills`, {
      data: { title: `${prefix} ${i}`, description: `Skill number ${i}`, body: `Instructions ${i}`, scope: "user" },
    });
    expect(res.ok()).toBeTruthy();
    ids.push((await res.json()).id);
  }
  return ids;
}

test.describe("Editing a skill", () => {
  test("the edit form comes into view and is ready to type in, however far down the pencil was", async ({ page }) => {
    const prefix = `Edit skill ${Date.now()}`;
    const ids = await createSkills(page, prefix, 12);
    try {
      await page.goto("/settings/skills");
      const last = page.getByText(`${prefix} 11`).first();
      await last.scrollIntoViewIfNeeded();
      const row = page.locator("div", { has: last }).filter({ has: page.getByTitle("Edit skill") }).last();

      await row.getByTitle("Edit skill").click();

      const form = page.getByTestId("skill-form");
      await expect(form).toBeInViewport({ ratio: 0.9 });
      await expect(form).toContainText("EDIT SKILL");
      await expect(form.getByLabel("Skill Title *")).toHaveValue(`${prefix} 11`);
      await expect(form.getByLabel("Skill Title *")).toBeFocused();

      await form.getByLabel("Skill Title *").fill(`${prefix} renamed`);
      await form.getByRole("button", { name: /save|update/i }).click();
      await expect(page.getByText(`${prefix} renamed`).first()).toBeVisible();
      await expect(form).toHaveCount(0);
    } finally {
      for (const id of ids) await page.request.delete(`${API}/v1/skills/${id}`);
    }
  });
});
