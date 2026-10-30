"use client";

import React, { useEffect, useState } from "react";

interface Match {
  index: number;
  role: string;
  excerpt: string;
  match_start: number;
  match_length: number;
  occurrences: number;
}

const SHOWN = 20;
const DEBOUNCE_MS = 300;

/** Matches of the search in the conversation's whole history (the server's, not only the
 * messages loaded here), each excerpt with the match highlighted. */
export const TranscriptSearchResults: React.FC<{ sessionId: string | null; query: string }> = ({ sessionId, query }) => {
  const [matches, setMatches] = useState<Match[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    const q = query.trim();
    if (!sessionId || !q) {
      setMatches([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetch(`/v1/sessions/${sessionId}/search?q=${encodeURIComponent(q)}&limit=100`, { signal: controller.signal })
        .then((res) => (res.ok ? res.json() : null))
        .then((body: { matches: Match[]; truncated: boolean } | null) => {
          setMatches(body?.matches ?? []);
          setTruncated(!!body?.truncated);
          setCurrent(0);
        })
        .catch(() => undefined);
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [sessionId, query]);

  if (!query.trim()) return null;
  const total = matches.reduce((sum, m) => sum + m.occurrences, 0);
  const start = Math.max(0, Math.min(current, matches.length - 1));
  const shown = matches.slice(start, start + SHOWN);
  return (
    <div
      data-testid="transcript-search-results"
      role="region"
      aria-label="Matches in the whole conversation"
      className="mt-1 max-h-72 w-72 overflow-y-auto rounded-lg border border-surface-border bg-surface-elevated p-2 text-xs shadow-md sm:w-96"
    >
      <div className="mb-1 flex items-center justify-between gap-2 text-neutral-700 dark:text-neutral-300">
        <span aria-live="polite">
          {matches.length === 0
            ? "No match in the conversation"
            : `${total} match${total === 1 ? "" : "es"} in ${matches.length}${truncated ? "+" : ""} message${matches.length === 1 ? "" : "s"}`}
        </span>
        {matches.length > SHOWN && (
          <span className="flex gap-1">
            <button type="button" aria-label="Previous matches" onClick={() => setCurrent((i) => Math.max(0, i - SHOWN))} className="rounded border border-border px-1.5">
              ‹
            </button>
            <button
              type="button"
              aria-label="Next matches"
              onClick={() => setCurrent((i) => Math.min(matches.length - 1, i + SHOWN))}
              className="rounded border border-border px-1.5"
            >
              ›
            </button>
          </span>
        )}
      </div>
      <ul className="space-y-1">
        {shown.map((match) => (
          <li key={match.index} className="rounded bg-surface-base px-2 py-1 text-foreground">
            <span className="mr-1 text-[10px] uppercase text-neutral-600 dark:text-neutral-400">{match.role}</span>
            {match.excerpt.slice(0, match.match_start)}
            <mark className="rounded bg-amber-300/70 px-0.5 text-foreground dark:bg-amber-500/40">
              {match.excerpt.slice(match.match_start, match.match_start + match.match_length)}
            </mark>
            {match.excerpt.slice(match.match_start + match.match_length)}
          </li>
        ))}
      </ul>
    </div>
  );
};
