import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL || "http://localhost:3000",
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
          reuseExistingServer: true,
          timeout: 60000,
          cwd: "../..",
        },
        {
          command: "pnpm dev",
          url: "http://localhost:3000",
          reuseExistingServer: true,
          timeout: 60000,
          env: {
            PLAYWRIGHT_TEST: "true",
            NEXTAUTH_URL: "http://localhost:3000",
            NEXTAUTH_SECRET: "test-secret-at-least-thirty-two-chars-long",
          },
        },
      ],
});
