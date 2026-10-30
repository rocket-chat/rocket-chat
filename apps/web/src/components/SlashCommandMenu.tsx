"use client";

import { useEffect, useMemo, useState } from "react";
import { BUILTIN_COMMANDS } from "../lib/composer-commands";

export interface SlashCommand {
  name: string;
  description: string;
  builtin: boolean;
}

const MAX_SHOWN = 8;

/** The commands that match what is typed after `/`: the composer's own, then the skills
 * (the organization's, the member's and the starter skills). */
export function useSlashMatches(input: string): SlashCommand[] {
  const [skills, setSkills] = useState<SlashCommand[] | null>(null);
  const active = /^\/[A-Za-z0-9_-]*$/.test(input);

  useEffect(() => {
    if (!active || skills !== null) return;
    let cancelled = false;
    Promise.all([
      fetch("/v1/skills").then((res) => (res.ok ? res.json() : [])).catch(() => []),
      fetch("/v1/skills/starter").then((res) => (res.ok ? res.json() : [])).catch(() => []),
    ]).then(([own, starter]: [{ name: string; description?: string; runtime?: string }[], { name: string; description: string; enabled: boolean }[]]) => {
      if (cancelled) return;
      const list: SlashCommand[] = [
        ...(Array.isArray(own) ? own : [])
          .filter((s) => (s.runtime ?? "text") === "text")
          .map((s) => ({ name: s.name, description: s.description ?? "", builtin: false })),
      ];
      for (const s of Array.isArray(starter) ? starter : []) {
        if (s.enabled && !list.some((c) => c.name === s.name)) list.push({ name: s.name, description: s.description, builtin: false });
      }
      setSkills(list);
    });
    return () => {
      cancelled = true;
    };
  }, [active, skills]);

  return useMemo(() => {
    if (!active) return [];
    const typed = input.slice(1).toLowerCase();
    const all = [...BUILTIN_COMMANDS.map((c) => ({ ...c, builtin: true })), ...(skills ?? [])];
    return all.filter((c) => c.name.toLowerCase().startsWith(typed)).slice(0, MAX_SHOWN);
  }, [active, input, skills]);
}

/** The matching commands above the composer; the composer moves `active` with the arrow keys. */
export function SlashCommandMenu({
  matches,
  active,
  onPick,
}: {
  matches: SlashCommand[];
  active: number;
  onPick: (command: SlashCommand) => void;
}) {
  if (matches.length === 0) return null;
  return (
    <ul
      id="slash-menu"
      role="listbox"
      aria-label="Commands and skills"
      data-testid="slash-menu"
      className="absolute bottom-full left-0 z-20 mb-1 w-80 overflow-hidden rounded-lg border border-border bg-surface-base shadow-xl"
    >
      {matches.map((c, index) => (
        <li key={`${c.builtin}-${c.name}`} id={`slash-option-${index}`} role="option" aria-selected={index === active}>
          <button
            type="button"
            data-testid="slash-option"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(c)}
            className={`w-full cursor-pointer px-3 py-1.5 text-left hover:bg-elevated ${index === active ? "bg-elevated" : ""}`}
          >
            <span className="font-mono text-xs text-foreground">/{c.name}</span>
            {c.description && <span className="ml-2 truncate text-[11px] text-neutral-600 dark:text-neutral-400">{c.description}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}
