import { RunningTurnData, SyncFrame, TelemetryFrame } from "../types/mission";

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
}

export function planSync(frame: SyncFrame, resumeRequested: boolean): SyncPlan {
  const lastSeq = typeof frame.last_seq === "number" ? frame.last_seq : null;
  const runningTurn = frame.running_turn ?? null;
  if (frame.catch_up) {
    return {
      rewindCursorTo: frame.catch_up.from_seq,
      events: [...frame.catch_up.events].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0)),
      continueFrom: lastSeq,
      runningTurn,
    };
  }
  // An idle session's snapshot is complete. A tab that asked to resume keeps its cursor and
  // lets the replay fill the gap; one that did not takes the server's mark.
  return {
    rewindCursorTo: null,
    events: [],
    continueFrom: resumeRequested ? null : lastSeq,
    runningTurn,
  };
}
