import { test, expect } from "@playwright/test";

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

  await page.goto(`/chat/${sessionId}`);
  const authors = page.getByTestId("user-message-author");
  await expect(authors).toHaveCount(2, { timeout: 20000 });
  // Signed in the author is "You"; otherwise a member shows their account name, which an
  // in-memory store does not keep (the id) and a database provisions from the id.
  await expect(authors.nth(0)).toHaveText(/^(You|dev_user|Developer|Dev User)$/);
  await expect(authors.nth(1)).toHaveText(/^(teammate_ana|Teammate Ana)$/);

  await page.reload();
  await expect(page.getByTestId("user-message-author")).toHaveCount(2, { timeout: 20000 });
});
