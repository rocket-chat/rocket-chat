# Web Mission Control: Modern AI Agent Design System

Rocket Chat's web interface (`apps/web`) is built with **Next.js 15 (App Router)**, **TypeScript**, **Tailwind CSS**, and **shadcn/ui**. It provides a clean, highly accessible, and polished AI agent experience inspired by modern developer platforms, featuring native light/dark mode, collapsible turn action groups, and tasteful rocket micro-animations.

---

## 1. Design Philosophy: Clean, Accessible & Modern

Rather than dense, boxy retro-avionics dashboards, Rocket Chat adopts a clean, distraction-free interface optimized for readability, workflow velocity, and multi-tenant agent orchestration.

> **Branding & AI Domain Notice**: The rocket motif is strictly visual branding and an identifiable UI theme (launch micro-animations, warm flame accents). The AI models, default agents, and execution contexts are strictly general-purpose software engineers, security specialists, and technical documentation writers. The AI does not possess or operate under any aerospace or avionics constraints.

### A. Core Visual Tokens & Palette

The design system uses modern neutral scales (Slate/Zinc) with warm rocket accents and dynamic CSS variables:

| Role | Light Mode Token | Dark Mode Token | Semantic Purpose |
| :--- | :--- | :--- | :--- |
| **`void` (Background)** | `#f8fafc` (Slate 50) | `#09090b` (Zinc 950) | Primary app canvas background. |
| **`surface`** | `#ffffff` (White) | `#121215` (Zinc 900) | Card backgrounds, sidebar, and dropdown containers. |
| **`elevated`** | `#f1f5f9` (Slate 100) | `#18181b` (Zinc 900) | Interactive item hovers, headers, and secondary decks. |
| **`overlay`** | `#e2e8f0` (Slate 200) | `#27272a` (Zinc 800) | Borders, dividers, subtle tags, and scrollbar thumbs. |
| **`primary` / `flame`** | `#ea580c` (Rocket Coral) | `#f97316` (Warm Orange) | Brand action buttons, active agent highlights, launch cues. |
| **`foreground`** | `#0f172a` (Slate 900) | `#f4f4f5` (Zinc 100) | High-contrast body typography. |
| **`muted`** | `#64748b` (Slate 500) | `#a1a1aa` (Zinc 400) | Secondary metadata, labels, and timestamps. |
| **`status-live`** | `#16a34a` (Emerald 600) | `#10b981` (Emerald 500) | Connected sandboxes, passing tests, and healthy sockets. |

### B. Native Light & Dark Mode Architecture

The application respects the user's OS color scheme by default and allows explicit override:
- **Zero-FOUC Initialization:** An inline script in `app/layout.tsx` checks `localStorage.getItem("theme_mode")` and `window.matchMedia("(prefers-color-scheme: dark)")` before first paint, eliminating flash of unstyled content.
- **Dynamic OS Listener:** Listens to system-level light/dark mode changes in real time.
- **Cycle Control:** The `ThemeToggle` component in both the header and sidebar enables users to cycle between `System`, `Light`, and `Dark`.

---

## 2. Cockpit Layout Architecture

The application layout consists of a collapsible sidebar, a central flight stream with collapsible action groups, a collapsible right-hand inspector panel, and a bottom input console:

```
+------------------------------------------------------------------------------------+
| HEADER: [Sidebar Toggle] • Brand • Target Branch • [Theme Toggle] • [Settings] • Status |
+------------------+--------------------------------------+--------------------------+
|                  |                                      |                          |
| COLLAPSIBLE      | CHAT FLIGHT STREAM                   | COLLAPSIBLE INSPECTOR    |
| SESSIONS SIDEBAR |                                      | [Diff View] | [Terminal] |
| - New Chat (+)   | - User Instruction Bubble            |                          |
| - Assigned Agent |                                      | Monaco DiffEditor        |
| - Chat Sessions  | ┌── COLLAPSIBLE ACTION GROUP ──────┐ | - Read-only diffs        |
| - User Profile   | │ "Executed 3 tools · 1.4s"    [v] │ | - Syntax-highlighted    |
| - Theme Toggle   | │ • Thinking / Reasoning trace     │ |                          |
|                  | │ • bash_exec `pytest tests/`      │ | @xterm/xterm Stream    |
|                  | └──────────────────────────────────┘ | - Live compiler stdout   |
|                  |                                      |                          |
|                  | - Agent Response (Markdown & Code)   |                          |
+------------------+--------------------------------------+--------------------------+
| CONSOLE: Agent/Model Switcher • Autosize Input • [Run ⌘⏎ with Rocket Micro-Animation]|
+------------------------------------------------------------------------------------+
```

---

## 3. Collapsible Action Groups & Streamlined Chat

To prevent chat streams from becoming overwhelmed by raw logs and execution noise, all intermediate steps between a user prompt and agent answers are aggregated into **Action Groups**:

1. **User Prompt:** Clean, right-aligned message bubble supporting full Markdown.
2. **Action Group (Always Collapsed by Default):**
   - Consolidates internal reasoning traces (`<think>` blocks) and all tool executions (`bash_exec`, `file_read`, `file_write`, `session_rename`, etc.).
   - Header badge indicates tool count, status, and execution duration (e.g., *"Executed 2 tools & analyzed reasoning (3 steps)"*).
   - Remains compactly collapsed by default even while being created and executed to conserve vertical space.
   - User can click anywhere on the header bar to expand and inspect tool arguments, output tabs, and thought processes.
3. **Agent Answer:** High-contrast, beautifully formatted response rendered via `MarkdownRenderer` with copyable code blocks.
4. **Subsequent Actions:** If the agent triggers further tool cycles, subsequent Action Groups appear sequentially between responses.

---

## 4. Rocket Animations & Visual Polish

The application incorporates polished, tasteful rocket-themed animations:
1. **First-Message Ignition & Liftoff:** When a user submits the very first prompt in a fresh session (`!flightLog.some(e => e.role === "USER")`), a full-view rocket liftoff overlay triggers (`animate-rocket-liftoff`), smoothly ascending with dual glowing exhaust plumes and soundless telemetry feedback.
2. **Thrust Pulse on Run Button:** Clicking the Run / Transmit button (or pressing `⌘⏎`) activates an orange-amber exhaust flare and recoil pulse (`animate-thrust-pulse`).
3. **Pulsing Rocket Loader:** While the agent executes in its sandbox, an animated rocket thruster with pulsating exhaust and telemetry dots replaces standard circular spinners (`THRUST ACTIVE / PROCESSING`).

---

## 5. URL Routing & Dynamic OpenRouter Model Catalog

1. **Deep-Linking & Routing (`/chat/[sessionId]`):**
   - Sessions are mapped directly to URL paths (`/chat/:sessionId`), enabling native browser history, bookmarking, and page refresh persistence.
   - The Cockpit synchronizes browser history state seamlessly as sessions are switched or created.
2. **Dynamic OpenRouter Catalog & Search:**
   - The backend `/v1/models` endpoint dynamically fetches available models from OpenRouter (`https://openrouter.ai/api/v1/models`) with an in-memory 1-hour cache and resilient offline fallback.
   - Context windows are strictly formatted as integers (preventing `NaNk ctx` errors).
   - The model selector includes an instant search filter to navigate across hundreds of available models effortlessly.
3. **Silent Session Auto-Titling:**
   - The agent is equipped with a `session_rename` FastMCP tool.
   - When a fresh session receives its first message, the platform silently derives a concise 3-5 word topic title from the user prompt and updates the session in real time via WebSocket `session_updated` events.
