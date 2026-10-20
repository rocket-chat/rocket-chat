"use client";

import React from "react";
import { File, Folder } from "lucide-react";
import type { MentionItem } from "./useMentions";

/** Suggestions for an `@path` mention; the composer owns the keyboard. */
export const MentionMenu: React.FC<{
  items: MentionItem[];
  active: number;
  onPick: (item: MentionItem) => void;
}> = ({ items, active, onPick }) => {
  if (items.length === 0) return null;
  return (
    <ul
      role="listbox"
      aria-label="Files and repositories"
      data-testid="mention-menu"
      className="absolute bottom-full left-0 z-20 mb-1 w-80 max-w-full overflow-hidden rounded-lg border border-border bg-surface-base shadow-xl"
    >
      {items.map((item, index) => (
        <li key={item.path} role="option" aria-selected={index === active}>
          <button
            type="button"
            data-testid="mention-option"
            onMouseDown={(event) => {
              // Keep the caret in the textarea.
              event.preventDefault();
              onPick(item);
            }}
            className={`flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-elevated ${index === active ? "bg-elevated" : ""}`}
          >
            {item.isDir ? <Folder className="h-3.5 w-3.5 text-amber-500" aria-hidden="true" /> : <File className="h-3.5 w-3.5 text-neutral-500" aria-hidden="true" />}
            <span className="truncate font-mono text-xs text-foreground">{item.path}{item.isDir ? "/" : ""}</span>
          </button>
        </li>
      ))}
    </ul>
  );
};
