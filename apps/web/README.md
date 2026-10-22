# `web` (Agent Mission Control & Cockpit)

Next.js 15 Web Application implementing the **Cosmic Telemetry & Avionics** design system for the Rocket Chat autonomous pair-programming platform.

## Overview

This application serves as the primary Mission Control cockpit for developers. Rather than a generic text editor, it focuses on real-time agent telemetry, interactive clarification cards, syntax-highlighted side-by-side diff review, and live container terminal streaming.

### Key Components

1. **Flight Telemetry Header (`FlightTelemetryHeader.tsx`):**
   - Displays project repository coordinates, git branch, active model badge, and live radar-ping sandbox status (`RUNNING` / `HIBERNATED`).
2. **Continuous Flight Stream (`FlightLogStream.tsx`):**
   - Chronological mission entries with aerospace role micro-badges (`[PLANNER]`, `[CODER]`, `[TESTER]`).
   - Collapsible reasoning trace (`<think>` block) with pulsating Ion Cyan glow (`animate-ion-pulse`).
3. **In-Stream Decision Cards (`DecisionCard.tsx`):**
   - Interactive single/multi-choice clarification cards with numeric keyboard shortcuts `[1]`, `[2]`, `[3]`.
   - Write-in custom steering instructions.
4. **The Ignition Console (`IgnitionConsole.tsx`):**
   - Bottom-docked launch deck with hotkey trigger `⌘⏎` / `Ctrl+Enter` and hyper-orange `[IGNITE MISSION]` button.
   - The first message of a session lifts the brand's rocket out of the Run button (`LaunchRocket.tsx`): `launchRocket(origin)` asks, `LaunchRocketHost` in the root layout draws the flight, so it outlives the page change that sending the message causes. Nothing is drawn for reduced motion.
5. **Monaco DiffEditor Panel (`MonacoDiffViewer.tsx`):**
   - Read-only side-by-side or inline code diff viewer with word-level highlight and file switcher tabs.
6. **Streaming Terminal Panel (`StreamingTerminal.tsx`):**
   - Read-only `@xterm/xterm` container rendering ANSI colored build and test execution logs directly from the Docker sandbox.

## Installation

```bash
pnpm install
```

## Running the Development Server

```bash
pnpm --filter web dev
```
Open [http://localhost:3000](http://localhost:3000) to view Mission Control.

## Verification & CI Checks

```bash
# Type check TypeScript
pnpm --filter web type-check

# Lint check (ESLint CLI, eslint.config.mjs)
pnpm --filter web lint

# Production build
pnpm --filter web build
```

## Environment Variables

- `NEXT_PUBLIC_API_URL`: (Optional) Base HTTP URL for the FastAPI Control Plane (default: `http://localhost:8000`).
- `NEXT_PUBLIC_WS_URL`: (Optional) Base WebSocket URL for telemetry streaming (default: `ws://localhost:8000`).

## Documentation images

`pnpm run docs:screenshots` regenerates the screenshots in `doc/assets/screenshots` and `pnpm run docs:demo` records the README's animated demo (`doc/assets/demo.gif`), both from the deterministic stack (stub model, fake sandbox). They are skipped in the normal suite (they need `DOCS_SCREENSHOTS=1`). Pillow, which assembles the GIF, is fetched on demand by `uv run --with pillow`.
