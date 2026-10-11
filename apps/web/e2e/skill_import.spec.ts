import { test, expect } from "@playwright/test";

const SKILL_MD = `---
name: e2e-release-notes
description: Draft release notes from merged pull requests
---

# Release notes

1. List merged PRs since the last tag.
`;

test.describe("Skills: SKILL.md import", () => {
  test("imports a SKILL.md file as a personal prompt skill", async ({ page, request }) => {
    await page.goto("/settings/skills");
    await page.getByRole("button", { name: /new (personal )?skill/i }).first().click();
    await page.getByTestId("skill-import-file").setInputFiles({
      name: "SKILL.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(SKILL_MD),
    });

    await expect(page.getByText(/Imported skill 'e2e-release-notes'/)).toBeVisible();

    const skills = await (await request.get("http://localhost:8000/v1/skills")).json();
    const imported = skills.find((s: { name: string }) => s.name === "e2e-release-notes");
    expect(imported.body).toContain("List merged PRs");
    await request.delete(`http://localhost:8000/v1/skills/${imported.id}`);
  });

  test("a malformed SKILL.md shows the reason instead of failing silently", async ({ page }) => {
    await page.goto("/settings/skills");
    await page.getByRole("button", { name: /new (personal )?skill/i }).first().click();
    await page.getByTestId("skill-import-file").setInputFiles({
      name: "SKILL.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("no front matter here"),
    });
    await expect(page.getByText(/front matter/i)).toBeVisible();
  });
});
