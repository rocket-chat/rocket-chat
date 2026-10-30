import { test, expect, type Page } from "@playwright/test";

// The e2e API acts as dev_user without signing in; tell the cockpit it is the person reading,
// so it can tell "You" from the others.
async function signInAsDevUser(page: Page): Promise<void> {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: { user_id: "dev_user", email: "dev@example.com", name: "Dev User", org_id: "default_org", roles: ["admin"] },
    })
  );
}

test("a shared session names who wrote each message, including after a reload", async ({
  page,
  request,
}) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: "Team mission", is_shared: true, initial_prompt: "First instruction from me" },
  });
  const sessionId: string = (await created.json()).session_id;

  // A teammate adds their own instruction to the shared session.
  await expect
    .poll(async () => {
      const res = await request.post(`http://localhost:8000/v1/sessions/${sessionId}/turns`, {
        headers: { "X-Tenant-User-Id": "teammate_ana", "X-User-Role": "developer" },
        data: { prompt: "Second instruction from Ana" },
      });
      return res.status();
    })
    .toBe(200);

  await signInAsDevUser(page);
  await page.goto(`/chat/${sessionId}`);
  const authors = page.getByTestId("user-message-author");
  await expect(authors).toHaveCount(2, { timeout: 20000 });
  // Your own message is "You"; another member shows their account name, which an in-memory
  // store does not keep (the id) and a database provisions from the id.
  await expect(authors.nth(0)).toHaveText("You");
  await expect(authors.nth(1)).toHaveText(/^(teammate_ana|Teammate Ana)$/);
  await expect(page.getByTestId("user-message").nth(0)).not.toHaveText(/teammate/);

  await page.reload();
  await expect(page.getByTestId("user-message-author")).toHaveCount(2, { timeout: 20000 });
});

test("an administrator who writes in a member's conversation is named there", async ({ page, request }) => {
  const created = await request.post("http://localhost:8000/v1/sessions", {
    data: { title: `Private mission ${Date.now()}`, initial_prompt: "My own instruction" },
  });
  const sessionId: string = (await created.json()).session_id;

  await expect
    .poll(async () => {
      const res = await request.post(`http://localhost:8000/v1/sessions/${sessionId}/turns`, {
        headers: { "X-Tenant-User-Id": "admin_grace", "X-User-Role": "admin" },
        data: { prompt: "An administrator's follow-up" },
      });
      return res.status();
    })
    .toBe(200);

  await signInAsDevUser(page);
  await page.goto(`/chat/${sessionId}`);
  const authors = page.getByTestId("user-message-author");
  await expect(authors).toHaveCount(2, { timeout: 20000 });
  await expect(authors.nth(0)).toHaveText("You");
  await expect(authors.nth(1)).toHaveText(/^(admin_grace|Admin Grace)$/);
});
