#!/usr/bin/env node
// rocket-browser-session serve | act '<json actions>'
//
// A browser that keeps its state between the agent's calls: one headless Chromium context
// (cookies, storage, the open page) served on a Unix socket inside the sandbox. `act` starts the
// server when it is not running, sends a list of actions and prints ONE JSON result. Actions:
//   {"do":"start","record":true}      record a video of the session (saved when it closes)
//   {"do":"navigate","url":"http://localhost:3000/"}
//   {"do":"click","selector":"text=Sign in"}   (any Playwright selector)
//   {"do":"type","selector":"#email","text":"a@b.c"}   {"do":"press","key":"Enter"}
//   {"do":"wait","ms":500}   {"do":"snapshot"}   {"do":"screenshot","path":"/workspace/..."}
//   {"do":"close"}
// The browser runs in the sandbox, so it reaches exactly what the sandbox may reach.
import http from "node:http";
import fs from "node:fs";
import { spawn } from "node:child_process";
import { chromium } from "playwright-core";

const SOCKET = process.env.ROCKET_BROWSER_SOCKET || "/tmp/rocket-browser.sock";
const VIDEO_DIR = "/workspace/.rocket/recordings";
const IDLE_EXIT_MS = 15 * 60 * 1000;
const clip = (text, limit) => (text.length > limit ? `${text.slice(0, limit)}\n… [truncated]` : text);

async function serve() {
  let browser = null;
  let context = null;
  let page = null;
  let recording = false;
  const consoleErrors = [];
  let idle = setTimeout(() => process.exit(0), IDLE_EXIT_MS);

  const open = async (record) => {
    if (page) return;
    browser = await chromium.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
    if (record) fs.mkdirSync(VIDEO_DIR, { recursive: true });
    context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      ...(record ? { recordVideo: { dir: VIDEO_DIR, size: { width: 1280, height: 800 } } } : {}),
    });
    recording = Boolean(record);
    page = await context.newPage();
    page.on("console", (m) => m.type() === "error" && consoleErrors.push(clip(m.text(), 300)));
    page.on("pageerror", (e) => consoleErrors.push(clip(String(e), 300)));
  };

  const close = async () => {
    const video = recording && page ? await page.video()?.path() : null;
    await context?.close();
    await browser?.close();
    browser = context = page = null;
    recording = false;
    return video;
  };

  const run = async (action) => {
    switch (action.do) {
      case "start":
        await close();
        await open(Boolean(action.record));
        return { started: true, recording };
      case "navigate":
        await open(false);
        return { status: (await page.goto(action.url, { waitUntil: "load", timeout: 30000 }))?.status() ?? null };
      case "click":
        await open(false);
        await page.locator(action.selector).first().click({ timeout: 10000 });
        return { clicked: action.selector };
      case "type":
        await open(false);
        await page.locator(action.selector).first().fill(String(action.text ?? ""), { timeout: 10000 });
        return { typed: action.selector };
      case "press":
        await open(false);
        await page.keyboard.press(String(action.key));
        return { pressed: action.key };
      case "wait":
        await open(false);
        await page.waitForTimeout(Math.min(Number(action.ms) || 500, 10000));
        return { waited: true };
      case "screenshot":
        await open(false);
        await page.screenshot({ path: action.path, fullPage: false });
        return { screenshot: action.path };
      case "snapshot":
        await open(false);
        return {
          url: page.url(),
          title: await page.title(),
          text: clip(await page.locator("body").innerText().catch(() => ""), 3000),
          accessibility: clip(await page.locator("body").ariaSnapshot().catch(() => ""), 4000),
        };
      case "close":
        return { closed: true, video: await close() };
      default:
        throw new Error(`Unknown action ${JSON.stringify(action.do)}`);
    }
  };

  if (fs.existsSync(SOCKET)) fs.unlinkSync(SOCKET);
  http
    .createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", async () => {
        clearTimeout(idle);
        idle = setTimeout(() => process.exit(0), IDLE_EXIT_MS);
        const results = [];
        try {
          for (const action of JSON.parse(body)) results.push({ do: action.do, ...(await run(action)) });
          const errors = consoleErrors.splice(0, 20);
          res.end(JSON.stringify({ results, url: page?.url() ?? null, console_errors: errors }));
        } catch (error) {
          res.end(JSON.stringify({ results, error: clip(String(error), 500), url: page?.url() ?? null }));
        }
      });
    })
    .listen(SOCKET);
}

function post(body) {
  return new Promise((resolve, reject) => {
    const req = http.request({ socketPath: SOCKET, method: "POST", path: "/" }, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => resolve(data));
    });
    req.on("error", reject);
    req.end(body);
  });
}

async function act(raw) {
  try {
    console.log(await post(raw));
    return;
  } catch {
    // Not running yet: start it, then retry for a few seconds while it opens its socket.
  }
  spawn(process.execPath, [process.argv[1], "serve"], { detached: true, stdio: "ignore" }).unref();
  for (let attempt = 0; attempt < 50; attempt += 1) {
    await new Promise((r) => setTimeout(r, 200));
    try {
      console.log(await post(raw));
      return;
    } catch {
      // still starting
    }
  }
  console.log(JSON.stringify({ error: "The browser session could not be started." }));
  process.exitCode = 1;
}

const [mode, payload] = process.argv.slice(2);
if (mode === "serve") await serve();
else if (mode === "act") await act(payload ?? "[]");
else {
  console.log(JSON.stringify({ error: "Usage: rocket-browser-session serve | act '<json actions>'" }));
  process.exitCode = 2;
}
