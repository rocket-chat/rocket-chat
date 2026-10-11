import { TelemetryFrame } from "../types/mission";

/**
 * Per-session WebSocket resume cursor.
 *
 * The cursor is the highest `seq` whose effect is already reflected in the cockpit
 * store: for the tab that applied it, the transcript is authoritative up to that
 * mark, so a reconnect asks the server for exactly the suffix it missed.
 *
 * It lives in memory (authoritative for the lifetime of the page context) and is
 * mirrored into `sessionStorage` so a reload does not discard the mark. Both are
 * keyed by session id: a tab can hold several sessions over its lifetime and each
 * carries its own sequence.
 */
const STORAGE_PREFIX = "rocket_ws_cursor:";

const memoryCursors = new Map<string, number>();

function storageKey(sessionId: string): string {
  return `${STORAGE_PREFIX}${sessionId}`;
}

function writeCursor(sessionId: string, seq: number): void {
  memoryCursors.set(sessionId, seq);
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(storageKey(sessionId), String(seq));
  } catch {
    // Private-mode / quota failures leave the in-memory cursor authoritative.
  }
}

/**
 * Highest applied `seq` for a session, or `null` when this tab has never applied
 * one (a fresh page must not claim knowledge it does not have).
 */
export function readCursor(sessionId: string): number | null {
  const cached = memoryCursors.get(sessionId);
  if (cached !== undefined) return cached;
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(storageKey(sessionId));
    if (!raw) return null;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || parsed <= 0) return null;
    memoryCursors.set(sessionId, parsed);
    return parsed;
  } catch {
    return null;
  }
}

/** Moves the cursor forward. Sequence numbers are monotonic, so it never rewinds. */
export function advanceCursor(sessionId: string, seq: number): void {
  if (!Number.isFinite(seq) || seq <= 0) return;
  const current = readCursor(sessionId);
  if (current !== null && seq <= current) return;
  writeCursor(sessionId, seq);
}

/**
 * Replaces the cursor with a server-provided baseline. Used when the retained
 * history is unusable (buffer wrapped, or our cursor is ahead of a restarted bus)
 * and the session snapshot becomes the new source of truth.
 */
export function adoptCursor(sessionId: string, seq: number): void {
  if (!Number.isFinite(seq) || seq <= 0) return;
  writeCursor(sessionId, seq);
}

/**
 * Sets the cursor to exactly ``seq`` - backwards too, and ``0`` forgets it. Used when the
 * transcript was rebuilt from a snapshot that reflects events only up to ``seq``.
 */
export function rewindCursor(sessionId: string, seq: number): void {
  if (!Number.isFinite(seq) || seq <= 0) {
    memoryCursors.delete(sessionId);
    if (typeof window === "undefined") return;
    try {
      window.sessionStorage.removeItem(storageKey(sessionId));
    } catch {
      // The in-memory cursor is already gone, which is what matters.
    }
    return;
  }
  writeCursor(sessionId, seq);
}

export function isAlreadyApplied(sessionId: string, seq: number): boolean {
  const current = readCursor(sessionId);
  return current !== null && seq <= current;
}

/**
 * The events from a `replay` frame that this tab has not seen yet, ascending by
 * `seq`. Events without a sequence number cannot be deduped and are always kept.
 */
export function selectUnseenEvents(
  events: TelemetryFrame[],
  appliedSeq: number | null
): TelemetryFrame[] {
  const ordered = [...events].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  const unseen: TelemetryFrame[] = [];
  let cursor = appliedSeq ?? 0;
  for (const event of ordered) {
    const seq = typeof event.seq === "number" && Number.isFinite(event.seq) ? event.seq : null;
    if (seq !== null) {
      if (seq <= cursor) continue;
      cursor = seq;
    }
    unseen.push(event);
  }
  return unseen;
}
