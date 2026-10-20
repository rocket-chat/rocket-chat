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

export function planSync(frame: SyncFrame): SyncPlan {
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
  // An idle session's snapshot is complete through `last_seq`: the record holds every answer
  // published before it. A resuming tab adopts that mark too, so the replay that follows
  // skips events the record already covers instead of appending them a second time.
  return {
    rewindCursorTo: null,
    events: [],
    continueFrom: lastSeq,
    runningTurn,
  };
}
