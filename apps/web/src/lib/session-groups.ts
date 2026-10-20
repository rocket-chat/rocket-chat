import type { SessionData } from "../types/mission";

export type SessionStatus = "needs_you" | "working" | "waiting" | "failed" | "idle";

/** One vocabulary for where a conversation stands, used by the sidebar and the header. */
export function statusOf(session: SessionData): SessionStatus {
  const meta = session.metadata ?? {};
  if (meta.awaiting_input) return "needs_you";
  if (session.is_processing) return "working";
  if (meta.last_turn_status === "error") return "failed";
  if (meta.last_turn_status === "waiting") return "waiting";
  return "idle";
}

export const STATUS_LABEL: Record<SessionStatus, string> = {
  needs_you: "Needs your answer",
  working: "Working",
  waiting: "Waiting for CI or a review",
  failed: "The last turn failed",
  idle: "",
};

function timeOf(session: SessionData): number | null {
  const raw = session.updated_at ?? session.created_at;
  if (!raw) return null;
  const value = /^\d+$/.test(String(raw)) ? Number(raw) * (String(raw).length <= 10 ? 1000 : 1) : Date.parse(String(raw));
  return Number.isFinite(value) ? value : null;
}

const DAY = 86_400_000;

export type GroupName = "Needs you" | "Pinned" | "Today" | "Yesterday" | "This week" | "Earlier";
export const GROUP_ORDER: GroupName[] = ["Needs you", "Pinned", "Today", "Yesterday", "This week", "Earlier"];

export function groupOf(session: SessionData, now: number = Date.now()): GroupName {
  if (statusOf(session) === "needs_you") return "Needs you";
  if (session.is_pinned) return "Pinned";
  const time = timeOf(session);
  if (time === null) return "Earlier";
  const startOfToday = new Date(now).setHours(0, 0, 0, 0);
  if (time >= startOfToday) return "Today";
  if (time >= startOfToday - DAY) return "Yesterday";
  if (time >= startOfToday - 6 * DAY) return "This week";
  return "Earlier";
}

/** "5 min ago", "3 h ago", "2 d ago": enough to tell recent work apart in a list. */
export function ago(session: SessionData, now: number = Date.now()): string {
  const time = timeOf(session);
  if (time === null) return "";
  const minutes = Math.max(0, Math.round((now - time) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / (60 * 24))} d ago`;
}

export function groupSessions(sessions: SessionData[], now: number = Date.now()): { name: GroupName; sessions: SessionData[] }[] {
  const buckets = new Map<GroupName, SessionData[]>();
  for (const session of sessions) {
    const name = groupOf(session, now);
    buckets.set(name, [...(buckets.get(name) ?? []), session]);
  }
  return GROUP_ORDER.filter((name) => buckets.has(name)).map((name) => ({ name, sessions: buckets.get(name)! }));
}
