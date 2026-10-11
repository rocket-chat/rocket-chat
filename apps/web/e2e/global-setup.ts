import { request } from "@playwright/test";

/**
 * Routes exercised by the E2E suite. Warm-up requests force the Next.js dev
 * server to compile each route before the first test navigates to it, which
 * removes the cold-compile stalls that previously tripped navigation timeouts.
 */
const ROUTES_TO_WARM = [
  "/",
  "/login",
  "/chat/draft",
  "/settings/agents",
  "/settings/github",
  "/settings/mcp",
  "/settings/subagents",
  "/settings/user/models",
];

export default async function globalSetup(): Promise<void> {
  const baseURL = process.env.PLAYWRIGHT_TEST_BASE_URL || "http://localhost:3000";
  const context = await request.newContext({ baseURL, timeout: 120_000 });
  try {
    for (const route of ROUTES_TO_WARM) {
      try {
        await context.get(route, { timeout: 120_000 });
      } catch {
        // Best-effort warm-up: an unreachable route must not abort the suite,
        // the test that needs it will fail with a far more useful error.
      }
    }
  } finally {
    await context.dispose();
  }
}
