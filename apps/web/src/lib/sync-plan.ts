import type { RunningTurnData, SyncFrame, TelemetryFrame } from "../types/mission";

/**
 * What a `sync` frame asks of the tab, beyond replacing the transcript with its snapshot.
 *
 * The snapshot is the session record, which does not yet hold the answer of a turn still
 * running (or one that ended while the record was being read). Replacing the transcript
 * therefore throws away anything this tab built from live events, so the cursor must go back
 * to the point the snapshot reflects and the events since must be folded in again - whatever
 * the tab had applied before. This matters when two connections share one cursor (a remount,
 * a reconnect): the second would otherwise skip events the first applied to a transcript that
 * no longer exists.
 */
export interface SyncPlan {
  /** Rewind the cursor to this sequence (0 clears it) before applying `events`. */
  rewindCursorTo: number | null;
  /** Events to apply on top of the snapshot, ascending. */
  events: TelemetryFrame[];
  /** The sequence the stream continues from once `events` are applied. */
  continueFrom: number | null;
  /** In-flight turn steps and state, recovered on mid-turn connect or refresh. */
  runningTurn?: RunningTurnData | null;
  /**
   * Streamed text (`token`, `reasoning_token`) up to this sequence is already in `runningTurn`;
   * the same events replayed or still arriving live must not add it again. Null without a turn.
   */
  textCoveredThrough: number | null;
}

const STREAMED_TEXT = new Set(["token", "reasoning_token"]);

/**
 * How far the running turn's snapshot covers what was streamed: through its own `seq` (what it
 * streamed since its last step is in `current_reasoning` / `current_text`), else through its
 * latest step (a step's `seq` is the sequence right after its `tool_completed`, and the text
 * before it is that step's reasoning and preface).
 */
export function textCoveredThrough(runningTurn: RunningTurnData | null): number | null {
  if (!runningTurn) return null;
  if (typeof runningTurn.seq === "number") return runningTurn.seq;
  return Math.max(0, ...(runningTurn.steps ?? []).map((step) => step.seq ?? 0));
}

/** Whether a frame repeats text the running turn's snapshot already shows. */
export function isCoveredText(event: Pick<TelemetryFrame, "event_type" | "seq">, coveredThrough: number | null): boolean {
  return coveredThrough !== null && STREAMED_TEXT.has(event.event_type) && (event.seq ?? 0) <= coveredThrough;
}

/**
 * The catch-up minus what the running turn's snapshot already shows. The catch-up starts where
 * the turn began, so without this every finished step would render twice: once from its record
 * and once rebuilt from its replayed tool and token events. Completions stay: applying one again
 * is harmless and clears the call's live output.
 */
function eventsNotInSnapshot(events: TelemetryFrame[], runningTurn: RunningTurnData | null): TelemetryFrame[] {
  if (!runningTurn) return events;
  const recordedIds = new Set((runningTurn.steps ?? []).map((step) => step.id).filter(Boolean));
  const covered = textCoveredThrough(runningTurn);
  return events.filter((event) => {
    if (event.event_type === "tool_started") return !recordedIds.has(String(event.payload?.id ?? ""));
    return !isCoveredText(event, covered);
  });
}

export function planSync(frame: SyncFrame): SyncPlan {
  const lastSeq = typeof frame.last_seq === "number" ? frame.last_seq : null;
  const runningTurn = frame.running_turn ?? null;
  if (frame.catch_up) {
    const ordered = [...frame.catch_up.events].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
    return {
      rewindCursorTo: frame.catch_up.from_seq,
      events: eventsNotInSnapshot(ordered, runningTurn),
      continueFrom: lastSeq,
      runningTurn,
      textCoveredThrough: textCoveredThrough(runningTurn),
    };
  }
  // An idle session's snapshot is complete through `last_seq`: the record holds every answer
  // published before it. A resuming tab adopts that mark too, so the replay that follows
  // skips events the record already covers instead of appending them a second time.
  return {
    rewindCursorTo: null,
    events: [],
    continueFrom: lastSeq,
    runningTurn,
    textCoveredThrough: textCoveredThrough(runningTurn),
  };
}
