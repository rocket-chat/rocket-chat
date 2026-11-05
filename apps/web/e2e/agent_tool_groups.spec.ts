import { test, expect, type Page } from "@playwright/test";

/**
 * The agent editor groups tools by the domain that declares them, and one group per MCP server.
 * Groups start collapsed; each header counts what is selected and takes the whole group at once,
 * and the core tools are one click away: "core + all of an MCP server" is three clicks.
 */
const GITHUB_MCP = {
  id: "e2e-github-mcp",
  name: "GitHub",
  transport: "streamable_http",
  endpoint_or_command: "https://mcp.example.test/github",
  status: "connected",
  tools_count: 3,
  tools: [
    { name: "list_issues", description: "List the issues of a repository" },
    { name: "create_issue", description: "Open an issue" },
    { name: "get_pull_request", description: "Read a pull request" },
  ],
};

async function openToolsOfNewAgent(page: Page, name: string): Promise<void> {
  await page.route("**/v1/mcp/servers", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: [GITHUB_MCP] })
      : route.fallback()
  );
  await page.goto("/settings/agents");
  await page.getByTestId("agent-new").click();
  await page.getByTestId("agent-name").fill(name);
  await page.getByTestId("agent-section-tools").click();
}

test.describe("Agent tool groups", () => {
  test("groups start collapsed and open from the keyboard", async ({ page }) => {
    await openToolsOfNewAgent(page, "Grouped Tools Agent");

    const header = page.getByTestId("agent-tool-group-header-git");
    await expect(header).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("agent-tool-git_status")).toBeHidden();
    // A new agent may use every tool: every group counts all its tools.
    await expect(page.getByTestId("agent-tool-group-count-git")).toHaveText(/^(\d+) \/ \1$/);

    await header.focus();
    await page.keyboard.press("Enter");
    await expect(header).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("agent-tool-git_status")).toBeVisible();
    await page.keyboard.press("Space");
    await expect(header).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("agent-tool-git_status")).toBeHidden();
  });

  test("core tools and a whole MCP server in three clicks, saved", async ({ page }) => {
    await openToolsOfNewAgent(page, "Core And GitHub Agent");

    await page.getByTestId("agent-allow-all").uncheck();
    await page.getByTestId("agent-add-core").click();
    await expect(page.getByTestId("agent-add-core")).toHaveText("Core tools added");
    await expect(page.getByTestId("agent-add-core")).toBeDisabled();
    const githubMcp = page.getByRole("checkbox", { name: "Add all GitHub MCP tools" });
    await githubMcp.click();

    // Done while collapsed: the header says so, and its checkbox now removes the group.
    await expect(page.getByTestId("agent-tool-group-header-mcp-github")).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("agent-tool-group-count-mcp-github")).toHaveText("3 / 3");
    await expect(page.getByRole("checkbox", { name: "Remove all GitHub MCP tools" })).toBeChecked();
    // Core tools span several domains: Git holds some of them, so it is partly selected.
    await expect(page.getByTestId("agent-tool-group-toggle-git")).toHaveJSProperty("indeterminate", true);

    const saved = page.waitForResponse(
      (res) => res.url().includes("/v1/agents") && res.request().method() === "POST"
    );
    await page.getByTestId("agent-save").click();
    const tools: string[] = (await (await saved).json()).whitelisted_tools;
    expect(tools).toEqual(
      expect.arrayContaining([
        "bash_exec",
        "git_commit",
        "wait_for",
        "mcp__github__list_issues",
        "mcp__github__create_issue",
        "mcp__github__get_pull_request",
      ])
    );
    expect(tools).not.toContain("git_status");
    expect(tools).not.toContain("*");
  });

  test("a group's checkbox adds and removes the whole group", async ({ page }) => {
    await openToolsOfNewAgent(page, "Git Only Agent");
    await page.getByTestId("agent-allow-all").uncheck();

    const count = page.getByTestId("agent-tool-group-count-git");
    await expect(count).toHaveText(/^0 \/ \d+$/);
    await page.getByRole("checkbox", { name: "Add all Git tools" }).click();
    const total = (await count.textContent())?.split(" / ")[1];
    await expect(count).toHaveText(`${total} / ${total}`);
    await page.getByTestId("agent-tool-group-header-git").click();
    await expect(page.getByTestId("agent-tool-git_status").getByRole("checkbox")).toBeChecked();

    await page.getByRole("checkbox", { name: "Remove all Git tools" }).click();
    await expect(count).toHaveText(`0 / ${total}`);
    await expect(page.getByTestId("agent-tool-git_status").getByRole("checkbox")).not.toBeChecked();
  });

  test("filtering opens the groups with a match and hides the rest", async ({ page }) => {
    await openToolsOfNewAgent(page, "Filtered Tools Agent");

    await page.getByLabel("Filter tools").fill("git_status");
    await expect(page.getByTestId("agent-tool-group-header-git")).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("agent-tool-git_status")).toBeVisible();
    await expect(page.getByTestId("agent-tool-git_commit")).toHaveCount(0);
    await expect(page.getByTestId("agent-tool-group-web")).toHaveCount(0);
    await expect(page.getByTestId("agent-tool-selected-count")).toBeVisible();

    await page.getByLabel("Filter tools").fill("");
    await expect(page.getByTestId("agent-tool-group-header-git")).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("agent-tool-group-web")).toBeVisible();
  });
});
