"use client";

import React, { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { renameSession } from "../lib/channels";
import { useMissionStore } from "../lib/store";

/** The session's title in the header; click it (or the pencil) to rename the session. */
export const SessionTitle: React.FC<{ title: string; sessionId: string | null }> = ({ title, sessionId }) => {
  const updateSessionTitle = useMissionStore((state) => state.updateSessionTitle);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [error, setError] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  const start = () => {
    if (!sessionId) return;
    setDraft(title);
    setError(false);
    setEditing(true);
  };

  const save = async () => {
    const next = draft.trim().slice(0, 120);
    setEditing(false);
    if (!sessionId || !next || next === title) return;
    updateSessionTitle(sessionId, next);
    if (!(await renameSession(sessionId, next))) {
      updateSessionTitle(sessionId, title);
      setError(true);
    }
  };

  if (editing) {
    return (
      <input
        ref={input}
        aria-label="Session title"
        data-testid="session-title-input"
        value={draft}
        maxLength={120}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => void save()}
        onKeyDown={(e) => {
          if (e.key === "Enter") void save();
          if (e.key === "Escape") setEditing(false);
        }}
        className="min-w-0 max-w-[34vw] rounded border border-brand/50 bg-surface-card px-1.5 py-0.5 text-sm font-semibold text-foreground outline-none sm:max-w-md"
      />
    );
  }
  return (
    <h1 className="group flex min-w-0 max-w-[34vw] items-center gap-1.5 truncate text-sm font-semibold text-foreground sm:max-w-md">
      <button
        type="button"
        onClick={start}
        disabled={!sessionId}
        title={sessionId ? "Rename" : undefined}
        data-testid="session-title"
        className="truncate text-left disabled:cursor-default"
      >
        {title}
      </button>
      {sessionId && (
        <button type="button" onClick={start} aria-label="Rename session" className="shrink-0 text-neutral-400 opacity-0 transition-opacity hover:text-brand group-hover:opacity-100 focus:opacity-100">
          <Pencil className="h-3 w-3" aria-hidden="true" />
        </button>
      )}
      {error && (
        <span role="alert" className="text-[11px] font-normal text-rose-500">
          Not renamed
        </span>
      )}
    </h1>
  );
};
