"use client";

import { useMissionStore } from "./store";

/** Commands the composer runs itself (never sent to the agent). */
export const BUILTIN_COMMANDS: { name: string; description: string }[] = [
  { name: "compact", description: "Compact the conversation's working context now" },
  { name: "model", description: "Switch the model: /model <name>" },
  { name: "plan", description: "Plan mode: read-only until you approve a plan" },
  { name: "agent", description: "Switch the agent: /agent <name>" },
  { name: "clear", description: "Start a new conversation" },
  { name: "cost", description: "What this conversation has cost so far" },
];
const NAMES = new Set(BUILTIN_COMMANDS.map((c) => c.name));

/** The built-in command a message is, or null for a message (or a skill) for the agent. */
export function builtinCommand(text: string): { name: string; argument: string } | null {
  const match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!match || !NAMES.has(match[1])) return null;
  return { name: match[1], argument: (match[2] ?? "").trim() };
}

function matchByName<T extends { id: string; name: string }>(items: T[], wanted: string): T | undefined {
  const lower = wanted.toLowerCase();
  return items.find((i) => i.id.toLowerCase() === lower) ?? items.find((i) => i.name.toLowerCase().includes(lower));
}

async function cost(sessionId: string): Promise<string> {
  const res = await fetch(`/v1/sessions/${sessionId}/usage`).catch(() => null);
  if (!res || !res.ok) return "The cost could not be read.";
  const usage = (await res.json()) as { totals: { cost_usd: number; total_tokens: number; calls: number } };
  const { cost_usd: dollars, total_tokens: tokens, calls } = usage.totals;
  return `This conversation: $${dollars.toFixed(2)}, ${tokens.toLocaleString()} tokens in ${calls} model calls.`;
}

async function compact(sessionId: string): Promise<string> {
  const res = await fetch(`/v1/sessions/${sessionId}/compact`, { method: "POST" }).catch(() => null);
  if (!res || !res.ok) return "The context could not be compacted.";
  const body = (await res.json()) as { status: string; message?: string };
  return body.status === "skipped" ? body.message ?? "Nothing to compact yet." : "Context compacted.";
}

/** Run a built-in command; what to tell the person. ``newConversation`` opens a blank one. */
export async function runBuiltin(
  command: { name: string; argument: string },
  newConversation: () => void,
): Promise<string> {
  const store = useMissionStore.getState();
  const sessionId = store.session && !store.session.is_draft ? store.session.session_id : null;
  switch (command.name) {
    case "clear":
      newConversation();
      return "";
    case "plan":
      store.setPermissionMode("plan");
      return "Plan mode: the agent investigates read-only and asks you to approve a plan.";
    case "model": {
      const found = matchByName(store.availableModels, command.argument);
      if (!command.argument || !found) return `No model matches "${command.argument}".`;
      store.selectDirectModel(found.id);
      return `Model: ${found.name}.`;
    }
    case "agent": {
      const found = matchByName(store.availableAgents, command.argument);
      if (!command.argument || !found) return `No agent matches "${command.argument}".`;
      store.selectAgent(found);
      return `Agent: ${found.name}.`;
    }
    case "cost":
      return sessionId ? cost(sessionId) : "Nothing has been spent in a new conversation.";
    case "compact":
      return sessionId ? compact(sessionId) : "There is nothing to compact yet.";
    default:
      return "";
  }
}
