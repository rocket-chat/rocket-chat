import type { Page } from "@playwright/test";

type Capability =
  | "create_personal_agents"
  | "bring_own_keys"
  | "use_bypass_mode"
  | "register_mcp"
  | "publish_as_bot"
  | "manage_spend_limits";

const ALL_CAPABILITIES: Capability[] = [
  "create_personal_agents",
  "bring_own_keys",
  "use_bypass_mode",
  "register_mcp",
  "publish_as_bot",
  "manage_spend_limits",
];

/**
 * Shows the page as a plain member of the development organization: the session (`/api/auth/me`)
 * and the role and capabilities the settings read (`/v1/me/access`). The API itself still sees
 * the development administrator, so a spec using this checks what the page shows, not what the
 * API enforces (the API's refusals are covered by `tests/security`).
 */
export async function asMember(page: Page, capabilities: Capability[] = ["bring_own_keys", "create_personal_agents"]): Promise<void> {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: { id: "m", user_id: "member_user", email: "m@example.com", name: "Member", org_id: "default_org", roles: ["member"] },
    }),
  );
  await page.route("**/v1/me/access", (route) =>
    route.fulfill({
      json: { org_id: "default_org", org_name: "Default organization", role: "member", roles: ["member"], capabilities },
    }),
  );
}

/** The development administrator, with every capability (the default identity, made explicit). */
export async function asAdmin(page: Page): Promise<void> {
  await page.route("**/v1/me/access", (route) =>
    route.fulfill({
      json: { org_id: "default_org", org_name: "Default organization", role: "admin", roles: ["admin"], capabilities: ALL_CAPABILITIES },
    }),
  );
}
