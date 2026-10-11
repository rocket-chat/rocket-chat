"use client";

import React, { useEffect, useState } from "react";

interface Command {
  name: string;
  description: string;
}

/**
 * Suggests prompt skills while the message starts with `/`. Choosing one fills in `/name `;
 * the server expands it, so the conversation still shows what was typed.
 */
export const SlashCommandMenu: React.FC<{
  input: string;
  onPick: (completion: string) => void;
}> = ({ input, onPick }) => {
  const [commands, setCommands] = useState<Command[]>([]);
  const active = /^\/[A-Za-z0-9_-]*$/.test(input);

  useEffect(() => {
    if (!active || commands.length > 0) return;
    let cancelled = false;
    fetch("/v1/skills")
      .then((res) => (res.ok ? res.json() : []))
      .then((skills: { name: string; description?: string; runtime?: string }[]) => {
        if (cancelled || !Array.isArray(skills)) return;
        setCommands(
          skills
            .filter((s) => (s.runtime ?? "text") === "text")
            .map((s) => ({ name: s.name, description: s.description ?? "" })),
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [active, commands.length]);

  if (!active) return null;
  const typed = input.slice(1).toLowerCase();
  const matches = commands.filter((c) => c.name.toLowerCase().startsWith(typed)).slice(0, 6);
  if (matches.length === 0) return null;

  return (
    <ul
      role="listbox"
      aria-label="Slash commands"
      data-testid="slash-menu"
      className="absolute bottom-full mb-1 left-0 z-20 w-72 rounded-lg border border-border bg-surface-base shadow-xl overflow-hidden"
    >
      {matches.map((c) => (
        <li key={c.name} role="option" aria-selected={false}>
          <button
            type="button"
            data-testid="slash-option"
            onClick={() => onPick(`/${c.name} `)}
            className="w-full text-left px-3 py-1.5 hover:bg-elevated cursor-pointer"
          >
            <span className="text-xs font-mono text-foreground">/{c.name}</span>
            {c.description && (
              <span className="ml-2 text-[11px] text-muted-foreground truncate">{c.description}</span>
            )}
          </button>
        </li>
      ))}
    </ul>
  );
};
