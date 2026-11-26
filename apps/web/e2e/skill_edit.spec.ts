import { test, expect, type Page } from "@playwright/test";
import { API_URL } from "./support/env";

async function createSkills(page: Page, prefix: string, count: number): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < count; i++) {
    const res = await page.request.post(`${API_URL}/v1/skills`, {
      data: { title: `${prefix} ${i}`, description: `Skill number ${i}`, body: `Instructions ${i}`, scope: "user" },
    });
    expect(res.ok()).toBeTruthy();
    ids.push((await res.json()).id);
  }
  return ids;
}

test.describe("Opening and closing skills", () => {
  test("the skill open on arrival can be closed, and every skill stays closed", async ({ page }) => {
    const prefix = `Close skill ${Date.now()}`;
    const ids = await createSkills(page, prefix, 2);
    try {
      await page.goto("/settings/skills");
      const opened = page.getByRole("button", { name: /^Hide the instructions of / });
      await expect(opened).toHaveCount(1);

      await opened.click();

      await expect(page.getByRole("button", { name: /^Show the instructions of / }).first()).toBeVisible();
      await expect(page.getByText("Skill Prompt Instructions:")).toHaveCount(0);
      // The list is fetched again after a close: nothing may reopen a skill on its own.
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("button", { name: /^Hide the instructions of / })).toHaveCount(0);

      await page.getByRole("button", { name: `Show the instructions of ${prefix} 1` }).click();
      await expect(page.getByRole("button", { name: `Hide the instructions of ${prefix} 1` })).toBeVisible();
    } finally {
      for (const id of ids) await page.request.delete(`${API_URL}/v1/skills/${id}`);
    }
  });
});

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
      for (const id of ids) await page.request.delete(`${API_URL}/v1/skills/${id}`);
    }
  });
});
