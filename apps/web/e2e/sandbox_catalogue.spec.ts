import { test, expect } from "@playwright/test";

test("an administrator adds a browser-capable sandbox and a member starts a session in it", async ({
  page,
  request,
}) => {
  const name = `Browser box ${Date.now()}`;
  await page.goto("/settings/org/sandboxes");

  const catalogue = page.getByTestId("sandbox-catalogue");
  await expect(catalogue).toBeVisible();
  await expect(catalogue.getByTestId("sandbox-row").first()).toContainText("Default");

  await page.getByTestId("sandbox-add").click();
  await page.getByTestId("sandbox-name").fill(name);
  await page.getByTestId("sandbox-image").fill("registry.example.com/team/browser-box:1");
  // The checkbox explains what the image must contain before anyone ticks it.
  await expect(page.getByTestId("sandbox-browser-tooltip")).toHaveAttribute(
    "title",
    /Chromium and Playwright/,
  );
  await page.getByTestId("sandbox-browser").check();
  await page.getByTestId("sandbox-save").click();

  const row = catalogue.getByTestId("sandbox-row").filter({ hasText: name });
  await expect(row).toContainText("browser");

  let createdId = "";
  try {
    await page.goto("/");
    const picker = page.getByTestId("sandbox-picker");
    await expect(picker).toBeVisible();
    await picker.selectOption({ label: name });
    await page.getByTestId("instruction-input").fill("hello");
    await page.getByTestId("ignite-button").click();
    await expect(page).toHaveURL(/\/chat\/(session_.+)/, { timeout: 15000 });
    const sessionId = page.url().split("/chat/")[1];

    const session = await (await request.get(`http://localhost:8000/v1/sessions/${sessionId}`)).json();
    expect(session.container_image).toBe("registry.example.com/team/browser-box:1");
    createdId = session.metadata.sandbox_id;
    expect(createdId).toMatch(/^sbx_/);
  } finally {
    const listed = await (await request.get("http://localhost:8000/v1/sandboxes")).json();
    for (const box of listed.sandboxes) {
      if (box.name === name) await request.delete(`http://localhost:8000/v1/admin/sandboxes/${box.id}`);
    }
  }
});

test("the default sandbox cannot be removed", async ({ page, request }) => {
  const listed = await (await request.get("http://localhost:8000/v1/sandboxes")).json();
  const fallback = listed.sandboxes.find((box: { is_default: boolean }) => box.is_default);
  const res = await request.delete(`http://localhost:8000/v1/admin/sandboxes/${fallback.id}`);
  expect(res.status()).toBe(409);
  await page.goto("/settings/org/sandboxes");
  await expect(page.getByTestId("sandbox-row").first().getByTestId("sandbox-remove")).toHaveCount(0);
});

test("the default sandbox's image can be edited from the settings page", async ({ page, request }) => {
  const listed = await (await request.get("http://localhost:8000/v1/sandboxes")).json();
  const original = listed.sandboxes.find((box: { is_default: boolean }) => box.is_default);
  const description = `Edited ${Date.now()}`;
  const replacement = "registry.example.com/team/devbox:2";

  await page.goto("/settings/org/sandboxes");
  const row = page.getByTestId("sandbox-row").first();
  await row.getByTestId("sandbox-edit").click();
  await expect(page.getByTestId("sandbox-image")).toHaveValue(original.image);
  await page.getByTestId("sandbox-image").fill(replacement);
  await page.getByRole("textbox", { name: "Description (optional)" }).fill(description);
  await page.getByTestId("sandbox-save").click();

  await expect(row).toContainText(replacement);
  await expect(row).toContainText(description);
  await expect(row).toContainText("Default");

  // Leave the shared default as it was found.
  const restored = await request.patch(`http://localhost:8000/v1/admin/sandboxes/${original.id}`, {
    data: { image: original.image, description: original.description },
  });
  expect(restored.ok()).toBeTruthy();
});

test("an administrator sizes a sandbox type and sets how long idle workspaces are kept", async ({
  page,
  request,
}) => {
  const name = `Big box ${Date.now()}`;
  // Profiles come from the operator's deployment; this one lists what the API would.
  await page.route("**/v1/admin/sandboxes/profiles", (route) =>
    route.fulfill({ json: [{ name: "gpu", description: "GPU pool" }] }),
  );
  await page.goto("/settings/org/sandboxes");

  await page.getByTestId("sandbox-add").click();
  await expect(page.getByTestId("sandbox-profile").locator("option")).toContainText([
    "Default placement",
    "gpu — GPU pool",
  ]);
  await page.getByTestId("sandbox-name").fill(name);
  await page.getByTestId("sandbox-image").fill("registry.example.com/team/big-box:1");
  await page.getByTestId("sandbox-cpu").fill("4");
  await page.getByTestId("sandbox-memory").fill("16Gi");
  await page.getByTestId("sandbox-release-days").fill("2");
  await page.getByTestId("sandbox-save").click();

  const row = page.getByTestId("sandbox-row").filter({ hasText: name });
  try {
    await expect(row).toContainText("4 CPU");
    await expect(row).toContainText("16Gi");
    await expect(row).toContainText("released after 2d");
  } finally {
    const listed = await (await request.get("http://localhost:8000/v1/sandboxes")).json();
    for (const box of listed.sandboxes) {
      if (box.name === name) await request.delete(`http://localhost:8000/v1/admin/sandboxes/${box.id}`);
    }
  }
});

test("a sandbox type's secret variable is write-only and kept when left blank", async ({ page, request }) => {
  const name = `Go box ${Date.now()}`;
  await page.goto("/settings/org/sandboxes");
  await page.getByTestId("sandbox-add").click();
  await page.getByTestId("sandbox-name").fill(name);
  await page.getByTestId("sandbox-image").fill("registry.example.com/team/go-box:1");
  const env = page.getByTestId("sandbox-env");
  await env.getByTestId("sandbox-env-add").click();
  await env.getByLabel("Variable 1 name").fill("GOPRIVATE");
  await env.getByLabel("Variable 1 value").fill("github.com/acme/*");
  await env.getByTestId("sandbox-env-add").click();
  await env.getByLabel("Variable 2 name").fill("NPM_TOKEN");
  await env.getByLabel("Variable 2 value").fill("npm_s3cret");
  await env.getByRole("checkbox", { name: "Secret" }).nth(1).check();
  await page.getByTestId("sandbox-save").click();

  const row = page.getByTestId("sandbox-row").filter({ hasText: name });
  try {
    await expect(row.getByTestId("sandbox-env-count")).toHaveText("2 variables");
    await expect(page.getByText("npm_s3cret")).toHaveCount(0);

    // Editing: the secret's value is never shown, and saving without retyping keeps it.
    await row.getByTestId("sandbox-edit").click();
    await expect(page.getByLabel("Variable 2 value")).toHaveValue("");
    await expect(page.getByLabel("Variable 2 value")).toHaveAttribute("placeholder", /Unchanged/);
    await page.getByTestId("sandbox-save").click();
    await expect(row.getByTestId("sandbox-env-count")).toHaveText("2 variables");

    const listed = await (await request.get("http://localhost:8000/v1/sandboxes")).json();
    const box = listed.sandboxes.find((b: { name: string }) => b.name === name);
    expect(box.env).toEqual([
      { name: "GOPRIVATE", value: "github.com/acme/*", secret: false },
      { name: "NPM_TOKEN", value: null, secret: true },
    ]);
  } finally {
    const listed = await (await request.get("http://localhost:8000/v1/sandboxes")).json();
    for (const box of listed.sandboxes) {
      if (box.name === name) await request.delete(`http://localhost:8000/v1/admin/sandboxes/${box.id}`);
    }
  }
});
