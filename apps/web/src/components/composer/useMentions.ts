"use client";

import { useEffect, useState } from "react";

export interface MentionItem {
  /** Path relative to /workspace (`api/src/main.py`). */
  path: string;
  name: string;
  isDir: boolean;
}

export interface MentionToken {
  /** Index of the `@` in the text. */
  start: number;
  query: string;
}

/** The `@path` being typed right before the caret, if any. */
export function mentionAt(text: string, caret: number): MentionToken | null {
  const match = /(^|\s)@([\w./-]*)$/.exec(text.slice(0, caret));
  if (!match) return null;
  return { start: caret - match[2].length - 1, query: match[2] };
}

/**
 * Files and repositories of the session's workspace that match what follows `@`: the top-level
 * folders first (the repositories), then the contents of whichever folder the path names.
 */
export function useMentions(sessionId: string | null, token: MentionToken | null): MentionItem[] {
  const [items, setItems] = useState<MentionItem[]>([]);
  const query = token?.query ?? null;

  useEffect(() => {
    if (!sessionId || query === null) {
      setItems([]);
      return;
    }
    let cancelled = false;
    const slash = query.lastIndexOf("/");
    const directory = slash >= 0 ? query.slice(0, slash) : "";
    const prefix = (slash >= 0 ? query.slice(slash + 1) : query).toLowerCase();
    // A short wait while typing: one read per pause, not one per key.
    const timer = setTimeout(() => {
      fetch(`/v1/sessions/${sessionId}/workspace/tree?path=${encodeURIComponent(directory || ".")}`)
        .then((res) => (res.ok ? res.json() : null))
        .then((body: { entries?: { name: string; path: string; is_dir: boolean }[] } | null) => {
          if (cancelled) return;
          const found = (body?.entries ?? [])
            .filter((entry) => entry.name.toLowerCase().startsWith(prefix))
            .slice(0, 8)
            .map((entry) => ({
              path: entry.path.replace(/^\/workspace\/?/, ""),
              name: entry.name,
              isDir: entry.is_dir,
            }));
          setItems(found);
        })
        .catch(() => !cancelled && setItems([]));
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionId, query]);

  return items;
}
