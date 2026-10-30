"use client";

import React, { useState } from "react";
import { Pencil, RotateCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { requestSend } from "../lib/cockpit-events";

const BUTTON = "cursor-pointer rounded-md p-1.5 text-neutral-600 hover:bg-surface-elevated hover:text-foreground disabled:opacity-50 dark:text-neutral-400";

/** Go back to just before turn ``turn`` (its message and everything after are removed), then
 * send ``prompt`` in its place. Turn 1 has nothing before it to go back to. */
async function replaceTurn(sessionId: string, turn: number, prompt: string): Promise<string | null> {
  if (turn < 2) return "The first message cannot be replaced; fork the conversation instead.";
  const res = await fetch(`/v1/sessions/${sessionId}/rewind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_turn: turn - 1 }),
  }).catch(() => null);
  if (!res || !res.ok) {
    const body = res ? ((await res.json().catch(() => ({}))) as { detail?: string }) : {};
    return typeof body.detail === "string" ? body.detail : "The conversation could not be rewound.";
  }
  const session = await fetch(`/v1/sessions/${sessionId}`).then((r) => (r.ok ? r.json() : null));
  if (session && session.session_id === sessionId) useMissionStore.getState().setSession(session);
  requestSend(prompt);
  return null;
}

/** Edit a message you sent and send it again in its place. */
export const UserMessageActions: React.FC<{ sessionId: string | null; turn: number; content: string; busy: boolean }> = ({
  sessionId,
  turn,
  content,
  busy,
}) => {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(content);
  const [error, setError] = useState("");
  if (!sessionId) return null;
  if (!editing) {
    return (
      <button
        type="button"
        data-testid="message-edit"
        aria-label="Edit and resend"
        title={turn < 2 ? "The first message cannot be edited; fork instead" : "Edit and resend"}
        disabled={busy || turn < 2}
        onClick={() => setEditing(true)}
        className={BUTTON}
      >
        <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    );
  }
  const submit = async () => {
    const problem = await replaceTurn(sessionId, turn, text.trim());
    if (problem) setError(problem);
    else setEditing(false);
  };
  return (
    <div className="mt-2 w-full space-y-1" data-testid="message-editor">
      <textarea
        aria-label="Edited message"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={Math.min(8, Math.max(2, text.split("\n").length))}
        className="w-full rounded border border-border bg-surface-base p-2 text-sm text-foreground"
      />
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-300">{error}</p>}
      <p className="text-xs text-neutral-600 dark:text-neutral-400">Later messages and their changes are removed.</p>
      <div className="flex gap-2">
        <button type="button" data-testid="message-resend" disabled={!text.trim()} onClick={() => void submit()} className="rounded bg-brand px-3 py-1 text-xs font-semibold text-white disabled:opacity-50">
          Send
        </button>
        <button type="button" onClick={() => setEditing(false)} className="rounded border border-border px-3 py-1 text-xs">
          Cancel
        </button>
      </div>
    </div>
  );
};

/** Rate an answer (with an optional comment) and regenerate it. */
export const AnswerActions: React.FC<{ sessionId: string | null; turn: number; prompt: string | null; busy: boolean }> = ({
  sessionId,
  turn,
  prompt,
  busy,
}) => {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState("");
  const [note, setNote] = useState("");
  if (!sessionId || turn < 1) return null;

  const rate = async (value: "up" | "down", text = "") => {
    const res = await fetch(`/v1/sessions/${sessionId}/feedback`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turn_number: turn, rating: value, comment: text }),
    }).catch(() => null);
    if (res?.ok) {
      setRating(value);
      setNote("Thanks, noted.");
    } else {
      setNote("The rating was not saved.");
    }
    setAsking(false);
  };

  const regenerate = async () => {
    if (!prompt) return;
    const problem = await replaceTurn(sessionId, turn, prompt);
    if (problem) setNote(problem);
  };

  return (
    <>
      <button type="button" data-testid="answer-thumbs-up" aria-label="Good answer" aria-pressed={rating === "up"} onClick={() => void rate("up")} className={BUTTON}>
        <ThumbsUp className={`h-3.5 w-3.5 ${rating === "up" ? "text-emerald-700 dark:text-emerald-400" : ""}`} aria-hidden="true" />
      </button>
      <button type="button" data-testid="answer-thumbs-down" aria-label="Bad answer" aria-pressed={rating === "down"} onClick={() => setAsking(true)} className={BUTTON}>
        <ThumbsDown className={`h-3.5 w-3.5 ${rating === "down" ? "text-red-700 dark:text-red-400" : ""}`} aria-hidden="true" />
      </button>
      {prompt && (
        <button
          type="button"
          data-testid="answer-regenerate"
          aria-label="Regenerate the answer"
          title={turn < 2 ? "The first answer cannot be regenerated; fork instead" : "Regenerate the answer"}
          disabled={busy || turn < 2}
          onClick={() => void regenerate()}
          className={BUTTON}
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      )}
      {asking && (
        <span className="flex items-center gap-1" data-testid="answer-feedback-form">
          <input
            aria-label="What was wrong (optional)"
            placeholder="What was wrong? (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void rate("down", comment)}
            className="w-48 rounded border border-border bg-surface-base px-1.5 py-0.5 text-xs"
          />
          <button type="button" onClick={() => void rate("down", comment)} className="rounded border border-border px-1.5 py-0.5 text-xs">
            Send
          </button>
        </span>
      )}
      {note && <span role="status" className="text-[11px] text-neutral-600 dark:text-neutral-400">{note}</span>}
    </>
  );
};
