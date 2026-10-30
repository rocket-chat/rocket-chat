import type { FlightLogEntry, SystemNotice } from "../types/mission";

type NoticeOf<K extends SystemNotice["kind"]> = Extract<SystemNotice, { kind: K }>;

const money = (value: number) => `$${value.toFixed(2)}`;

/** The one-line headline of a compaction: what was compressed, and whether the summary was rebuilt. */
export function compactionHeadline(notice: NoticeOf<"compaction">): string {
  const rebuilt = notice.rebuilt
    ? " The model's summary was unusable, so it was rebuilt from the tool history."
    : "";
  return (
    `Compressed ${notice.compactedMessages} messages from ${notice.tokensBefore.toLocaleString()} ` +
    `to ${notice.tokensAfter.toLocaleString()} tokens.${rebuilt}`
  );
}

/** Plain text for a notice: what search, copy and the transcript fallback read. No emoji, no jargon. */
export function noticeText(notice: SystemNotice): string {
  switch (notice.kind) {
    case "compaction":
      return `Context compacted: ${compactionHeadline(notice)}${notice.summary ? `\n\n${notice.summary}` : ""}`;
    case "continuation":
      return `The task continues (${notice.index}/${notice.max}): ${notice.reason}`;
    case "step_limit":
      return `${notice.reason} after ${notice.steps} steps: the agent is writing its answer from what it gathered.`;
    case "alert":
      return notice.text;
    case "turn_failed":
      return `The turn failed: ${notice.message}`;
    case "sandbox_failed":
      return notice.message;
    case "send_failed":
      return `Your message could not be sent: ${notice.message}`;
    case "turn_stopped":
      return "You stopped the turn.";
    case "spend_warning":
      return `${notice.label}: ${money(notice.spent)} of ${money(notice.amount)} used.`;
    case "cost":
      return (
        `This conversation has cost ${money(notice.costUsd)} so far: ` +
        `${notice.totalTokens.toLocaleString()} tokens over ${notice.calls} model calls in ${notice.turns} turns.`
      );
    case "info":
      return notice.text;
  }
}

/** A conversation entry for a notice: the card reads `notice`, everything else reads `content`. */
export function noticeEntry(id: string, notice: SystemNotice, timestamp: string): FlightLogEntry {
  return { id, role: "SYSTEM", content: noticeText(notice), timestamp, notice };
}

export type LegacyNoticeKind = "compaction" | "continuation" | "alert" | "step_limit";

/**
 * Which card a system message restored from history stands for. Stored messages are text only
 * (older ones carry emoji and the old wording), so this is the one place that still reads text;
 * live events carry a typed `notice` instead.
 */
export function legacyNoticeKind(content: string): LegacyNoticeKind | null {
  const lower = content.toLowerCase();
  if (/^\s*context compact(ed|ion)\b/i.test(content)) return "compaction";
  if (lower.includes("continuation") || lower.startsWith("the task continues")) return "continuation";
  if (lower.includes("cache collapse") || lower.includes("alert:")) return "alert";
  if (content.includes("⚡") || content.includes("Turn Budget Exceeded") || content.includes("Synthesizing final answer")) {
    return "step_limit";
  }
  return null;
}

/** A stored system line without the emoji older versions put in front of it. */
export function withoutLeadingEmoji(content: string): string {
  return content.replace(/^[\p{Extended_Pictographic}\uFE0F\s]+/u, "");
}
