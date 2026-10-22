import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  // Zero retries: the suite is deterministic (stubbed model, fake sandbox, no
  // time-based waits), so a failure must always be a real regression.
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  // Next.js compiles routes on first request. The default 5s assertion budget is
  // routinely exceeded by that first cold compile, so give interactions and
  // whole tests a budget that reflects compilation rather than UI latency.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL || "http://localhost:3000",
    actionTimeout: 15_000,
    navigationTimeout: 45_000,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.PLAYWRIGHT_TEST_BASE_URL
    ? undefined
    : [
        {
          command: "PYTHONPATH=apps/api/src uv run uvicorn api.main:app --port 8000",
          url: "http://127.0.0.1:8000/v1/models",
          // Always start our own server locally: a stale dev server would run with
          // different env, so reusing one would silently change test behaviour.
          reuseExistingServer: false,
          timeout: 120000,
          cwd: "../..",
          env: {
            // Deterministic model + sandbox: no provider calls, no Docker.
            ROCKET_MODEL_STUB: "deterministic",
            DEFAULT_SANDBOX_DRIVER: "fake",
            // In-memory session store by default: the suite needs no database and cannot
            // be affected by a developer's exported DATABASE_URL. Set E2E_DATABASE_URL (the
            // row-level-security application role) and E2E_ADMIN_DATABASE_URL to run the very
            // same suite against a migrated PostgreSQL - the production-like configuration.
            DATABASE_URL: process.env.E2E_DATABASE_URL ?? "",
            ADMIN_DATABASE_URL: process.env.E2E_ADMIN_DATABASE_URL ?? "",
            TEST_DATABASE_URL: "",
            DEV_AUTH_BYPASS: "true",
            // Specs act as other members through identity headers; on PostgreSQL their user
            // rows must exist (sessions reference users), so unknown identities are created.
            ROCKET_AUTO_PROVISION_TENANTS: "true",
            // The MCP runner started below: command (stdio) servers are exercised for real.
            MCP_RUNNER_URL: "http://127.0.0.1:8100",
            MCP_RUNNER_TOKEN: "e2e-runner-token-0123456789",
          },
        },
        {
          command: "uv run python -m mcp_runner",
          url: "http://127.0.0.1:8100/health",
          reuseExistingServer: false,
          timeout: 120000,
          cwd: "../..",
          env: {
            MCP_RUNNER_TOKEN: "e2e-runner-token-0123456789",
            MCP_RUNNER_CACHE_DIR: "/tmp/rocket-e2e-mcp-cache",
            MCP_RUNNER_WORK_DIR: "/tmp/rocket-e2e-mcp-work",
          },
        },
        {
          command: "pnpm dev",
          url: "http://localhost:3000",
          // Also always fresh: the app must run with PLAYWRIGHT_TEST=true, so a
          // pre-existing dev server without it would redirect every test to /login.
          reuseExistingServer: false,
          timeout: 120000,
          env: {
            PLAYWRIGHT_TEST: "true",
            NEXTAUTH_URL: "http://localhost:3000",
            NEXTAUTH_SECRET: "test-secret-at-least-thirty-two-chars-long",
          },
        },
      ],
});
