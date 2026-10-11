#!/usr/bin/env node
// rocket-browser <url> [--screenshot <path>] [--wait <ms>]
//
// Loads a page in headless Chromium and prints ONE JSON object describing what a person would
// see: status, title, visible text, an accessibility snapshot, console errors and failed
// requests, and optionally a screenshot. The agent's `browser_inspect` tool runs this inside
// the sandbox, so the browser only ever reaches what the sandbox itself can reach.
import { chromium } from "playwright-core";

const args = process.argv.slice(2);
const url = args.find((a) => !a.startsWith("--"));
const option = (name, fallback) => {
  const at = args.indexOf(`--${name}`);
  return at >= 0 && args[at + 1] ? args[at + 1] : fallback;
};
if (!url || !/^https?:\/\//.test(url)) {
  console.log(JSON.stringify({ error: "Usage: rocket-browser <http(s) url> [--screenshot path] [--wait ms]" }));
  process.exit(2);
}

const clip = (text, limit) => (text.length > limit ? `${text.slice(0, limit)}\n… [truncated]` : text);
const consoleErrors = [];
const failedRequests = [];
let browser;
try {
  browser = await chromium.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(clip(m.text(), 300)));
  page.on("pageerror", (e) => consoleErrors.push(clip(String(e), 300)));
  page.on("requestfailed", (r) => failedRequests.push(`${r.method()} ${r.url()} ${r.failure()?.errorText ?? ""}`.trim()));

  const response = await page.goto(url, { waitUntil: "load", timeout: 30000 });
  await page.waitForTimeout(Number(option("wait", "500")));

  const result = {
    url: page.url(),
    status: response ? response.status() : null,
    title: await page.title(),
    text: clip(await page.locator("body").innerText().catch(() => ""), 4000),
    accessibility: clip(await page.locator("body").ariaSnapshot().catch(() => ""), 6000),
    console_errors: consoleErrors.slice(0, 20),
    failed_requests: failedRequests.slice(0, 20),
  };
  const shot = option("screenshot", "");
  if (shot) {
    await page.screenshot({ path: shot, fullPage: false });
    result.screenshot = shot;
  }
  console.log(JSON.stringify(result));
} catch (error) {
  console.log(JSON.stringify({ error: clip(String(error), 500), url }));
  process.exitCode = 1;
} finally {
  await browser?.close();
}
