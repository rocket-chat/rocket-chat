"use client";

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useShallow } from "zustand/react/shallow";
import { Bot, CornerDownLeft, MessageSquare, Search, Settings, Zap } from "lucide-react";
import { useMissionStore } from "../lib/store";
import { useAccess } from "../lib/access";
import { requestCockpit } from "../lib/cockpit-events";
import { useSessionSearch } from "../lib/useSessionSearch";
import type { SessionData } from "../types/mission";
import { NAV_SECTIONS } from "./settings/nav-data";
import { visible } from "./settings/SettingsNav";

type Group = "Actions" | "Conversations" | "Agents" | "Settings";

interface PaletteItem {
  id: string;
  group: Group;
  title: string;
  detail?: string;
  shortcut?: string;
  run: () => void;
}

const GROUP_ICON: Record<Group, React.ComponentType<{ className?: string }>> = {
  Actions: Zap,
  Conversations: MessageSquare,
  Agents: Bot,
  Settings: Settings,
};

const MAX_PER_GROUP = 8;

function matches(text: string, words: string[]): boolean {
  const lower = text.toLowerCase();
  return words.every((word) => lower.includes(word));
}

/** The actions the palette always offers, built from the store's own setters. */
function useActions(close: () => void): PaletteItem[] {
  const router = useRouter();
  const { initDraftSession, openInspectorTab, setIsTurnInspectorOpen, setIsMemoryInspectorOpen, isLeftSidebarOpen, setIsLeftSidebarOpen } =
    useMissionStore(
      useShallow((state) => ({
        initDraftSession: state.initDraftSession,
        openInspectorTab: state.openInspectorTab,
        setIsTurnInspectorOpen: state.setIsTurnInspectorOpen,
        setIsMemoryInspectorOpen: state.setIsMemoryInspectorOpen,
        isLeftSidebarOpen: state.isLeftSidebarOpen,
        setIsLeftSidebarOpen: state.setIsLeftSidebarOpen,
      }))
    );
  return useMemo(() => {
    const act = (id: string, title: string, run: () => void, shortcut?: string): PaletteItem => ({
      id: `action-${id}`,
      group: "Actions",
      title,
      shortcut,
      run: () => {
        close();
        run();
      },
    });
    return [
      act("new", "New conversation", () => {
        initDraftSession();
        router.push("/chat/draft");
      }, "⌘⇧O"),
      act("search", "Search this conversation", () => requestCockpit("open-transcript-search")),
      act("changes", "Show changes", () => openInspectorTab("changes")),
      act("git", "Show the branch and pull request", () => openInspectorTab("git")),
      act("files", "Show files", () => openInspectorTab("files")),
      act("processes", "Show processes", () => openInspectorTab("processes")),
      act("artifacts", "Show artifacts", () => openInspectorTab("artifacts")),
      act("preview", "Show the app preview", () => openInspectorTab("preview")),
      act("turns", "Turn history", () => setIsTurnInspectorOpen(true)),
      act("memory", "Memory", () => setIsMemoryInspectorOpen(true)),
      act("sidebar", isLeftSidebarOpen ? "Hide the sidebar" : "Show the sidebar", () => setIsLeftSidebarOpen(!isLeftSidebarOpen)),
    ];
  }, [close, router, initDraftSession, openInspectorTab, setIsTurnInspectorOpen, setIsMemoryInspectorOpen, isLeftSidebarOpen, setIsLeftSidebarOpen]);
}

/** Everything the palette can offer for `query`, grouped and capped per group. */
function usePaletteItems(query: string, open: boolean, close: () => void): PaletteItem[] {
  const router = useRouter();
  const access = useAccess();
  const { sessions, agents, selectAgent } = useMissionStore(
    useShallow((state) => ({ sessions: state.sessions, agents: state.availableAgents, selectAgent: state.selectAgent }))
  );
  const actions = useActions(close);
  const serverSessions = useSessionSearch(query, { enabled: open, limit: MAX_PER_GROUP });

  return useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const go = (href: string) => () => {
      close();
      router.push(href);
    };
    const known = new Map<string, SessionData>();
    for (const s of [...sessions, ...serverSessions]) {
      if (!s.is_draft && s.session_id !== "draft") known.set(s.session_id, s);
    }
    const conversations: PaletteItem[] = [...known.values()]
      .filter((s) => matches(`${s.title ?? ""} ${s.git_repo ?? ""}`, words))
      .map((s) => ({ id: `session-${s.session_id}`, group: "Conversations", title: s.title || s.session_id, detail: s.git_repo ?? undefined, run: go(`/chat/${s.session_id}`) }));
    const agentItems: PaletteItem[] = agents
      .filter((a) => a.enabled !== false && matches(`${a.name} ${a.role_title ?? ""}`, words))
      .map((a) => ({
        id: `agent-${a.id}`,
        group: "Agents",
        title: `Use ${a.name}`,
        detail: a.role_title,
        run: () => {
          close();
          selectAgent(a);
        },
      }));
    const settings: PaletteItem[] = NAV_SECTIONS.flatMap((section) =>
      section.items
        .filter((item) => visible(item, access))
        .map((item) => ({ id: `settings-${item.href}`, group: "Settings" as const, title: item.label, detail: section.title, run: go(item.href) }))
    ).filter((item) => matches(`${item.title} ${item.detail} settings`, words));
    const cap = (items: PaletteItem[]) => items.slice(0, MAX_PER_GROUP);
    return [
      ...cap(actions.filter((a) => matches(a.title, words))),
      ...cap(conversations),
      ...cap(agentItems),
      ...cap(settings),
    ];
  }, [query, sessions, serverSessions, agents, access, actions, close, router, selectAgent]);
}

/**
 * The cockpit's command palette (⌘K / Ctrl+K): conversations, agents, settings pages and
 * actions in one keyboard-driven list. Inside the settings, ⌘K searches the settings instead.
 */
export const CommandPalette: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const listId = useId();
  const close = useCallback(() => setOpen(false), []);
  const items = usePaletteItems(query, open, close);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      restoreFocus.current = document.activeElement as HTMLElement | null;
      input.current?.focus();
      return;
    }
    setQuery("");
    setActive(0);
    restoreFocus.current?.focus?.();
  }, [open]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(i + 1, items.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      items[active]?.run();
    }
  };

  if (!open) return null;
  const optionId = (index: number) => `${listId}-option-${index}`;

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 p-4 pt-[12vh] backdrop-blur-sm" onMouseDown={(event) => event.target === event.currentTarget && setOpen(false)}>
      <div role="dialog" aria-modal="true" aria-label="Command palette" data-testid="command-palette" className="w-full max-w-xl overflow-hidden rounded-2xl border border-surface-border bg-surface-base shadow-2xl">
        <div className="flex items-center gap-2 border-b border-surface-border px-4 py-3">
          <Search className="h-4 w-4 text-neutral-500" aria-hidden="true" />
          <input
            ref={input}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={items[active] ? optionId(active) : undefined}
            aria-autocomplete="list"
            aria-label="Search conversations, agents, settings and actions"
            data-testid="command-palette-input"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder="Search conversations, agents, settings and actions"
            className="flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-neutral-500"
          />
          <kbd className="rounded border border-surface-border px-1.5 text-[10px] text-neutral-500">Esc</kbd>
        </div>
        <ul id={listId} role="listbox" aria-label="Results" className="max-h-[55vh] overflow-y-auto p-1.5">
          {items.length === 0 && (
            <li role="presentation" className="px-3 py-6 text-center text-xs text-neutral-500">
              Nothing matches “{query}”.
            </li>
          )}
          {items.map((item, index) => {
            const Icon = GROUP_ICON[item.group];
            return (
              <li
                key={item.id}
                id={optionId(index)}
                role="option"
                aria-selected={index === active}
                data-testid="command-palette-option"
                data-group={item.group}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => item.run()}
                className={`flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 ${index === active ? "bg-surface-elevated" : ""}`}
              >
                <Icon className="h-3.5 w-3.5 shrink-0 text-neutral-500" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-foreground">{item.title}</span>
                  <span className="block truncate text-[11px] text-neutral-500">{[item.group, item.detail].filter(Boolean).join(" · ")}</span>
                </span>
                {item.shortcut && <kbd className="rounded border border-surface-border px-1.5 text-[10px] text-neutral-500">{item.shortcut}</kbd>}
                {index === active && <CornerDownLeft className="h-3.5 w-3.5 text-neutral-500" aria-hidden="true" />}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
};
