/**
 * Where the suite's servers listen. The defaults are what CI uses; a developer whose 3000 or 8000
 * is taken by another project sets E2E_WEB_PORT / E2E_API_PORT instead of
 * stopping it. Playwright starts every server itself on these ports, and the specs that talk to
 * the API directly read the same values, so the two can never disagree.
 */
const port = (name: string, fallback: number): number => Number(process.env[name] ?? fallback);

export const WEB_PORT = port("E2E_WEB_PORT", 3000);
export const API_PORT = port("E2E_API_PORT", 8000);

export const WEB_URL = process.env.PLAYWRIGHT_TEST_BASE_URL || `http://localhost:${WEB_PORT}`;
export const API_URL = `http://localhost:${API_PORT}`;
