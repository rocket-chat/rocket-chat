import { test, expect } from "@playwright/test";

test("typing / offers prompt skills and the conversation keeps what was typed", async ({
  page,
  request,
}) => {
  const created = await request.post("http://localhost:8000/v1/skills", {
    data: {
      name: "e2e-review",
      description: "Review the given path",
      body: "Review $ARGUMENTS for bugs.",
      scope: "user",
    },
  });
  expect(created.ok()).toBeTruthy();
  const skillId: string = (await created.json()).id;

  try {
    await page.goto("/");
    const input = page.getByTestId("instruction-input");
    await input.fill("/e2e-r");
    const option = page.getByTestId("slash-option").first();
    await expect(option).toContainText("/e2e-review");
    await option.click();
    await expect(input).toHaveValue("/e2e-review ");

    await input.fill("/e2e-review src/");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/session_.+/, { timeout: 15000 });
    // The cockpit shows the command as typed; the model got the expanded skill.
    await expect(page.getByText("/e2e-review src/").first()).toBeVisible();
    // The deterministic stub model echoes the instruction it received: the expanded skill.
    await expect(page.getByText(/Stub answer.*Review src\/ for bugs\./).first()).toBeVisible({
      timeout: 15000,
    });
  } finally {
    await request.delete(`http://localhost:8000/v1/skills/${skillId}`);
  }
});
