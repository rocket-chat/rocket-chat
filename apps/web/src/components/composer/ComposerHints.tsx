"use client";

import React, { useEffect, useRef, useState } from "react";
import { Keyboard } from "lucide-react";

const SEEN_KEY = "rocket.composerHintsSeen";

const HINTS: [string, string][] = [
  ["Enter", "Run (or send to the running agent)"],
  ["Shift + Enter", "New line"],
  ["/", "Run a skill"],
  ["@", "Point at a file or repository"],
  ["Tab", "Accept the suggested next prompt"],
  ["⌘ K", "New conversation"],
];

/** The composer's keyboard shortcuts: shown once by themselves, then one click away. */
export const ComposerHints: React.FC = () => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      if (!localStorage.getItem(SEEN_KEY)) {
        setOpen(true);
        localStorage.setItem(SEEN_KEY, "1");
      }
    } catch {
      // Storage can be blocked: the hints are then only one click away.
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        data-testid="composer-hints"
        aria-label="Keyboard shortcuts"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="rounded-lg p-1.5 text-muted-foreground hover:bg-elevated hover:text-foreground"
        title="Keyboard shortcuts"
      >
        <Keyboard className="h-4 w-4" />
      </button>
      {open && (
        <div role="dialog" aria-label="Keyboard shortcuts" className="absolute bottom-full right-0 z-30 mb-2 w-72 rounded-xl border border-border bg-surface-base p-3 shadow-xl">
          <p className="mb-2 text-xs font-medium text-foreground">Shortcuts</p>
          <dl className="space-y-1.5">
            {HINTS.map(([keys, what]) => (
              <div key={keys} className="flex items-baseline justify-between gap-3 text-[11px]">
                <dt><kbd className="rounded border border-border px-1.5 py-0.5 font-mono text-[10px] text-foreground">{keys}</kbd></dt>
                <dd className="text-right text-muted-foreground">{what}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
};
