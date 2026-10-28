import { test, expect } from "@playwright/test";
import { isCoveredText, planSync } from "../src/lib/sync-plan";
import {
  adoptCursor,
  advanceCursor,
  readCursor,
  rewindCursor,
  selectUnseenEvents,
} from "../src/lib/session-cursor";
import {
  convertHistoryToFlightLogs,
  formatTimestamp,
  formatEventTime,
} from "../src/lib/store/helpers";
import type { SyncFrame, TelemetryFrame } from "../src/types/mission";

const event = (seq: number, type = "token"): TelemetryFrame =>
  ({ event_type: type, session_id: "s", payload: {}, seq }) as TelemetryFrame;

test.describe("what a sync frame asks of the tab", () => {
  test("a running turn's events are folded in even when this tab thinks it already applied them", () => {
    const frame = {
      type: "sync",
      last_seq: 8,
      catch_up: { from_seq: 0, events: [event(3), event(1, "status_changed"), event(2)] },
    } as SyncFrame;

    // A second connection resumes from the cursor a first one advanced to 8, then the
    // snapshot (which has no answer yet) replaces the transcript.
    const plan = planSync(frame);

    expect(plan.rewindCursorTo).toBe(0);
    expect(plan.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(plan.continueFrom).toBe(8);
  });

  test("the cursor really goes back, so the events apply again", () => {
    const id = "session_sync_plan";
    advanceCursor(id, 8);
    expect(readCursor(id)).toBe(8);

    rewindCursor(id, 0);
    expect(readCursor(id)).toBeNull();
    expect(selectUnseenEvents([event(1), event(2)], readCursor(id)).length).toBe(2);

    adoptCursor(id, 5);
    rewindCursor(id, 2);
    expect(readCursor(id)).toBe(2);
    expect(selectUnseenEvents([event(1), event(2), event(3)], readCursor(id)).map((e) => e.seq)).toEqual([3]);
  });

  test("a running turn's recorded steps are not rebuilt again from its replayed events", () => {
    const frame = {
      type: "sync",
      last_seq: 6,
      running_turn: {
        turn_index: 1,
        started_at: 1,
        current_reasoning: "next",
        steps: [{ id: "call_1", name: "bash_exec", status: "success", seq: 4, reasoning: "first" }],
      },
      catch_up: {
        from_seq: 0,
        events: [
          event(2, "reasoning_token"),
          { ...event(3, "tool_started"), payload: { id: "call_1" } },
          { ...event(4, "tool_completed"), payload: { id: "call_1" } },
          { ...event(5, "tool_started"), payload: { id: "call_2" } },
          event(6, "reasoning_token"),
        ],
      },
    } as SyncFrame;

    const plan = planSync(frame);

    // Without its own sequence the snapshot covers through its latest step: text up to it is in
    // the record, as is the call it made. A call still running, the completion (idempotent) and
    // the thought since then are not.
    expect(plan.textCoveredThrough).toBe(4);
    expect(plan.events.map((e) => e.seq)).toEqual([4, 5, 6]);
  });

  test("a snapshot taken at a sequence covers the text streamed up to it, replayed or live", () => {
    const frame = {
      type: "sync",
      last_seq: 9,
      running_turn: {
        turn_index: 1,
        started_at: 1,
        seq: 7,
        current_reasoning: "weighing two fixes",
        current_text: "The cause is",
        steps: [{ id: "call_1", name: "bash_exec", status: "success", seq: 4 }],
      },
      catch_up: {
        from_seq: 0,
        events: [event(6, "reasoning_token"), event(7, "token"), event(8, "token"), event(9, "plan_updated")],
      },
    } as SyncFrame;

    const plan = planSync(frame);

    expect(plan.runningTurn?.current_reasoning).toBe("weighing two fixes");
    expect(plan.events.map((e) => e.seq)).toEqual([8, 9]);
    // The same rule for frames still arriving live, from another replica for instance.
    expect(isCoveredText({ event_type: "token", seq: 7 }, plan.textCoveredThrough)).toBe(true);
    expect(isCoveredText({ event_type: "token", seq: 8 }, plan.textCoveredThrough)).toBe(false);
    expect(isCoveredText({ event_type: "tool_started", seq: 5 }, plan.textCoveredThrough)).toBe(false);
  });

  test("an idle session's snapshot is complete through last_seq, for resuming and fresh tabs alike", () => {
    const frame = { type: "sync", last_seq: 12, catch_up: null } as SyncFrame;
    expect(planSync(frame)).toEqual({
      rewindCursorTo: null,
      events: [],
      continueFrom: 12,
      runningTurn: null,
      textCoveredThrough: null,
    });
  });

  test("running_turn data is passed through to the plan", () => {
    const runningTurn = {
      turn_index: 2,
      started_at: 1000,
      model: "gpt-4o",
      status: "running",
      steps: [{ name: "bash_exec", status: "completed", result: "ok" }],
    };
    const frame = { type: "sync", last_seq: 5, catch_up: null, running_turn: runningTurn } as SyncFrame;
    const plan = planSync(frame);
    expect(plan.runningTurn).toEqual(runningTurn);
  });
});

test.describe("flight log and historical timestamps", () => {
  test("convertHistoryToFlightLogs preserves message and tool timestamps without defaulting to now", () => {
    const historicalTime = 1700000000;
    const toolTime = 1700000030;
    const expectedMsgTime = formatTimestamp(historicalTime);
    const expectedToolTime = formatTimestamp(toolTime);

    const history = [
      {
        role: "user",
        content: "What is the status?",
        created_at: historicalTime,
      },
      {
        role: "assistant",
        content: "Here is the status.",
        created_at: historicalTime + 60,
        tool_calls: [
          {
            name: "bash_exec",
            arguments: { command: "uptime" },
            created_at: toolTime,
          },
        ],
      },
    ];

    const logs = convertHistoryToFlightLogs(history, "session_test");
    expect(logs.length).toBe(3);

    expect(logs[0].timestamp).toBe(expectedMsgTime);
    expect(logs[0].timestamp.length).toBeGreaterThan(0);

    expect(logs[1].timestamp).toBe(expectedToolTime);
    expect(logs[1].timestamp.length).toBeGreaterThan(0);

    expect(logs[2].timestamp).toBe(formatTimestamp(historicalTime + 60));
  });

  test("formatEventTime formats timestampMs if present and falls back if absent", () => {
    const epochSec = 1700000000;
    expect(formatEventTime(epochSec)).toBe(formatTimestamp(epochSec));
    expect(formatEventTime(epochSec * 1000)).toBe(formatTimestamp(epochSec));
    expect(formatEventTime(null).length).toBeGreaterThan(0);
    expect(formatEventTime(undefined).length).toBeGreaterThan(0);
  });
});

