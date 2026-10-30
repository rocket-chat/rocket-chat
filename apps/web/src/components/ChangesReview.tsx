"use client";

import React, { useCallback, useEffect, useState } from "react";
import { MessageSquarePlus, Send, Undo2, X } from "lucide-react";
import { requestSend } from "../lib/cockpit-events";

interface Hunk {
  index: number;
  header: string;
  added: number;
  removed: number;
  preview: string;
}

interface ReviewComment {
  path: string;
  line: number | null;
  text: string;
}

interface ChangesReviewProps {
  sessionId: string;
  repository: string;
  path: string;
  scope: string;
  /** Called once a hunk was undone, to reload the diff. */
  onChanged: () => void;
}

/** Comments anchored to a file and line, collected into one review the agent reads; and the
 * file's changed regions, each of which can be undone alone. */
export const ChangesReview: React.FC<ChangesReviewProps> = ({ sessionId, repository, path, scope, onChanged }) => {
  const [hunks, setHunks] = useState<Hunk[]>([]);
  const [error, setError] = useState("");
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [line, setLine] = useState("");
  const [text, setText] = useState("");

  const loadHunks = useCallback(async () => {
    const query = new URLSearchParams({ repository, path, scope });
    const res = await fetch(`/v1/sessions/${sessionId}/changes/hunks?${query}`).catch(() => null);
    setHunks(res && res.ok ? await res.json() : []);
  }, [sessionId, repository, path, scope]);

  useEffect(() => {
    void loadHunks();
  }, [loadHunks]);

  const undo = async (index: number) => {
    setError("");
    const res = await fetch(`/v1/sessions/${sessionId}/changes/revert-hunk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repository, path, scope, index }),
    }).catch(() => null);
    if (!res || !res.ok) {
      const body = res ? ((await res.json().catch(() => ({}))) as { detail?: string }) : {};
      setError(body.detail ?? "Could not undo that change.");
      return;
    }
    await loadHunks();
    onChanged();
  };

  const addComment = () => {
    if (!text.trim()) return;
    const parsed = Number.parseInt(line, 10);
    setComments((list) => [...list, { path, line: Number.isFinite(parsed) && parsed > 0 ? parsed : null, text: text.trim() }]);
    setText("");
    setLine("");
  };

  const sendReview = () => {
    const lines = comments.map((c) => `- ${c.path}${c.line ? `:${c.line}` : ""}: ${c.text}`);
    requestSend(`Review of your changes; address each comment:\n${lines.join("\n")}`);
    setComments([]);
  };

  return (
    <div data-testid="changes-review" className="space-y-2 border-t border-border bg-surface/40 p-2 text-xs">
      {hunks.length > 0 && (
        <details>
          <summary className="cursor-pointer text-neutral-700 dark:text-neutral-300">
            {hunks.length} changed region{hunks.length === 1 ? "" : "s"} in this file
          </summary>
          <ul className="mt-1 max-h-48 space-y-1 overflow-y-auto">
            {hunks.map((hunk) => (
              <li key={hunk.index} className="rounded border border-border bg-surface-base p-1.5">
                <div className="flex items-center gap-2 font-mono">
                  <span className="truncate text-neutral-700 dark:text-neutral-300">{hunk.header}</span>
                  <span className="text-diff-add">+{hunk.added}</span>
                  <span className="text-diff-del">-{hunk.removed}</span>
                  <button
                    type="button"
                    data-testid={`hunk-undo-${hunk.index}`}
                    onClick={() => void undo(hunk.index)}
                    className="ml-auto flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:bg-surface-elevated"
                  >
                    <Undo2 className="h-3 w-3" aria-hidden="true" /> Undo
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && <p role="alert" className="text-diff-del">{error}</p>}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-neutral-700 dark:text-neutral-300">Line</span>
          <input
            value={line}
            onChange={(e) => setLine(e.target.value)}
            inputMode="numeric"
            className="w-16 rounded border border-border bg-surface-base px-1.5 py-1"
          />
        </label>
        <label className="flex min-w-[12rem] flex-1 flex-col gap-0.5">
          <span className="text-neutral-700 dark:text-neutral-300">Comment on {path}</span>
          <input
            data-testid="review-comment-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addComment()}
            className="rounded border border-border bg-surface-base px-1.5 py-1"
          />
        </label>
        <button type="button" data-testid="review-comment-add" onClick={addComment} className="flex items-center gap-1 rounded border border-border px-2 py-1 hover:bg-surface-elevated">
          <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden="true" /> Add
        </button>
      </div>
      {comments.length > 0 && (
        <div className="space-y-1">
          <ul className="space-y-0.5">
            {comments.map((c, i) => (
              <li key={i} className="flex items-center gap-1 text-foreground">
                <span className="font-mono">{c.path}{c.line ? `:${c.line}` : ""}</span>: {c.text}
                <button type="button" aria-label="Remove the comment" onClick={() => setComments((list) => list.filter((_, j) => j !== i))} className="ml-auto rounded p-0.5 hover:bg-surface-elevated">
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
          <button type="button" data-testid="review-send" onClick={sendReview} className="flex items-center gap-1 rounded bg-brand px-2.5 py-1 font-semibold text-white">
            <Send className="h-3.5 w-3.5" aria-hidden="true" /> Send review ({comments.length})
          </button>
        </div>
      )}
    </div>
  );
};
