import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { test, expect, type APIRequestContext } from "@playwright/test";

const API = "http://localhost:8000";

/** A real armored private key from GnuPG, made in a short temporary home (agent sockets live there). */
function makeKey(uid: string, passphrase: string): string {
  const home = mkdtempSync("/tmp/e2e-gpg-");
  const gpg = (...args: string[]) =>
    execFileSync("gpg", ["--homedir", home, "--batch", "--pinentry-mode", "loopback", ...args], {
      encoding: "utf8",
    });
  try {
    gpg("--passphrase", passphrase, "--quick-generate-key", uid, "ed25519", "sign", "never");
    const fingerprint = gpg("--list-keys", "--with-colons").split("fpr:::::::::")[1].split(":")[0];
    return gpg("--passphrase", passphrase, "--armor", "--export-secret-keys", fingerprint);
  } finally {
    try {
      execFileSync("gpgconf", ["--homedir", home, "--kill", "all"]);
    } catch {
      // no agent was started
    }
    rmSync(home, { recursive: true, force: true });
  }
}

async function setSigningMode(request: APIRequestContext, mode: string): Promise<void> {
  const saved = await request.put(`${API}/v1/admin/settings/github`, {
    data: { config: { commit_signing_mode: mode } },
  });
  expect(saved.ok()).toBeTruthy();
}

test.describe("Commit signing", () => {
  test.afterEach(async ({ request }) => {
    await request.delete(`${API}/v1/user/git-signing`);
    await setSigningMode(request, "github_app");
  });

  test("explains who signs the commits when the organization signs through GitHub", async ({ page }) => {
    await page.goto("/settings/github");
    await expect(page.getByTestId("git-signing-mode-note")).toContainText("GitHub signs them");
    await expect(page.getByTestId("git-signing-private-key")).toHaveCount(0);
    await expect(page.getByTestId("commit-identity")).toContainText("attributed to");
  });

  test("a member imports a GPG key, sees what to upload to GitHub, and removes it", async ({ page, request }) => {
    await setSigningMode(request, "gpg_key");
    const key = makeKey("E2E Signer <signer@example.com>", "pw");

    await page.goto("/settings/github");
    await page.getByTestId("git-signing-private-key").fill(key);
    await page.getByTestId("git-signing-passphrase").fill("wrong");
    await page.getByTestId("git-signing-import").click();
    await expect(page.getByTestId("git-signing-error")).toContainText("passphrase");

    await page.getByTestId("git-signing-passphrase").fill("pw");
    await page.getByTestId("git-signing-import").click();
    const stored = page.getByTestId("git-signing-key");
    await expect(stored).toContainText("signer@example.com");
    await expect(page.getByTestId("git-signing-public-key")).toContainText("BEGIN PGP PUBLIC KEY BLOCK");
    await expect(page.getByText("PRIVATE KEY BLOCK")).toHaveCount(0);

    await page.reload();
    await expect(page.getByTestId("git-signing-key")).toContainText("signer@example.com");

    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: /Remove key/ }).click();
    await expect(page.getByTestId("git-signing-private-key")).toBeVisible();
  });
});
