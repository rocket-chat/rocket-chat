import type { MissionStore } from "./store/types";

export type SessionUpdate = Parameters<MissionStore["applySessionUpdate"]>[1];

/** The fields of a `session_updated` payload the list shows, each only when it is present. */
export function sessionUpdateFrom(payload: Record<string, unknown>): SessionUpdate {
  const text = (key: string) => (typeof payload[key] === "string" ? (payload[key] as string) : undefined);
  const flag = (key: string) => (typeof payload[key] === "boolean" ? (payload[key] as boolean) : undefined);
  return {
    title: text("title"),
    is_pinned: flag("is_pinned"),
    is_archived: flag("is_archived"),
    is_processing: flag("is_processing"),
    awaiting_input: text("awaiting_input"),
    last_turn_status: text("last_turn_status"),
  };
}
