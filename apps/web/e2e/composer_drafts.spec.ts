import { test, expect, type Page } from "@playwright/test";

const BACKEND = "http://localhost:8000";

async function createSession(page: Page, title: string): Promise<string> {
  const res = await page.request.post(`${BACKEND}/v1/sessions`, { data: { title } });
  return (await res.json()).session_id as string;
}

const composer = (page: Page) => page.getByTestId("instruction-input");

// Opening another conversation from the list is a client-side navigation: the composer stays
// mounted, which is where stale text used to leak from.
async function openFromList(page: Page, sessionId: string): Promise<void> {
  await page.locator(`[data-testid="session-row"][data-session-id="${sessionId}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/chat/${sessionId}`));
}

test.describe("The composer", () => {
  test("a starter prompt fills the field once, not every conversation opened afterwards", async ({ page }) => {
    const other = await createSession(page, `Other ${Date.now()}`);
    await page.goto("/");
    await page.getByTestId("starter-prompt").first().click();
    await expect(composer(page)).not.toHaveValue("");

    await openFromList(page, other);

    await expect(composer(page)).toHaveValue("");
  });

  test("unsent text stays with its own conversation", async ({ page }) => {
    const first = await createSession(page, `First ${Date.now()}`);
    const second = await createSession(page, `Second ${Date.now()}`);
    await page.goto(`/chat/${first}`);
    await composer(page).fill("half-written thought for the first conversation");
    // The draft is saved a moment after typing.
    await expect
      .poll(() => page.evaluate((id) => localStorage.getItem(`rocket:draft:${id}`), first))
      .toContain("half-written");

    await openFromList(page, second);
    await expect(composer(page)).toHaveValue("");

    await openFromList(page, first);
    await expect(composer(page)).toHaveValue("half-written thought for the first conversation");
  });
});
