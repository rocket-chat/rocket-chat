"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CornerDownLeft, Search } from "lucide-react";
import { NAV_SECTIONS } from "./nav-data";
import { settingLabel } from "../../lib/settings-labels";

interface SchemaField {
  key: string;
  description: string;
  policy: "instance" | "org" | "org_default" | "member";
  label?: string;
  deprecated?: boolean;
}

interface Result {
  id: string;
  kind: "page" | "setting";
  title: string;
  detail: string;
  href: string;
}

// The page a domain's settings are edited on (member-scoped keys live on the personal pages).
const DOMAIN_PAGE: Record<string, { href: string; where: string }> = {
  general: { href: "/settings/org", where: "General" },
  models: { href: "/settings/org/models", where: "Models & credentials" },
  budgets: { href: "/settings/org/spend", where: "Spend" },
  governance: { href: "/settings/org/governance", where: "Security & data" },
  memory: { href: "/settings/org/governance", where: "Security & data" },
  sandboxes: { href: "/settings/org/sandboxes", where: "Sandboxes" },
  github: { href: "/settings/org/git", where: "Git & GitHub" },
  mcp: { href: "/settings/mcp", where: "MCP servers" },
  slack: { href: "/settings/slack", where: "Slack" },
  access: { href: "/settings/org/users", where: "Members & roles" },
  user_preferences: { href: "/settings/user/preferences", where: "Preferences" },
};

function matches(text: string, words: string[]): boolean {
  const lower = text.toLowerCase();
  return words.every((word) => lower.includes(word));
}

/** Find a page or a single setting by what it is called or what it does (⌘K). */
export const SettingsSearchButton: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [fields, setFields] = useState<{ domain: string; field: SchemaField }[]>([]);
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open || fields.length > 0) return;
    fetch("/v1/settings-schema")
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { domains?: Record<string, SchemaField[]> } | null) => {
        if (!body?.domains) return;
        setFields(
          Object.entries(body.domains).flatMap(([domain, list]) =>
            list.filter((field) => !field.deprecated).map((field) => ({ domain, field })),
          ),
        );
      })
      .catch(() => undefined);
  }, [open, fields.length]);

  useEffect(() => {
    if (open) input.current?.focus();
    else {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  const results = useMemo<Result[]>(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const pages: Result[] = NAV_SECTIONS.flatMap((section) =>
      section.items.map((item) => ({ id: item.href, kind: "page" as const, title: item.label, detail: section.title, href: item.href })),
    ).filter((page) => matches(`${page.title} ${page.detail}`, words));
    if (words.length === 0) return pages;
    const settings: Result[] = fields
      .filter(({ field }) => matches(`${settingLabel(field)} ${field.key} ${field.description}`, words))
      .slice(0, 12)
      .map(({ domain, field }) => {
        const page = DOMAIN_PAGE[domain] ?? { href: "/settings/org", where: "Settings" };
        return {
          id: `${domain}.${field.key}`,
          kind: "setting" as const,
          title: settingLabel(field),
          detail: `${page.where} · ${field.description}`,
          href: `${page.href}#setting-${domain}-${field.key}`,
        };
      });
    return [...pages, ...settings];
  }, [query, fields]);

  const go = useCallback(
    (result: Result | undefined) => {
      if (!result) return;
      setOpen(false);
      router.push(result.href);
      // A link to the page already open does not reload it: tell the form to look again.
      if (result.href.includes("#")) setTimeout(() => window.dispatchEvent(new HashChangeEvent("hashchange")), 100);
    },
    [router],
  );

  return (
    <>
      <button
        type="button"
        data-testid="settings-search-open"
        onClick={() => setOpen(true)}
        className="flex w-full items-center gap-2 rounded-lg border border-surface-border bg-surface-card/60 px-3 py-2 text-xs text-neutral-600 hover:border-brand/40 hover:text-foreground dark:text-neutral-300"
      >
        <Search className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="flex-1 text-left">Search settings</span>
        <kbd className="rounded border border-surface-border px-1.5 text-[10px] text-neutral-500">⌘K</kbd>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 p-4 pt-[12vh] backdrop-blur-sm"
          onClick={() => setOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Search settings"
            data-testid="settings-search-dialog"
            onClick={(event) => event.stopPropagation()}
            className="w-full max-w-xl overflow-hidden rounded-2xl border border-surface-border bg-surface-base shadow-2xl"
          >
            <div className="flex items-center gap-2 border-b border-surface-border px-4 py-3">
              <Search className="h-4 w-4 text-neutral-500" aria-hidden="true" />
              <input
                ref={input}
                data-testid="settings-search-input"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActive(0);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setOpen(false);
                  else if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setActive((i) => Math.min(i + 1, results.length - 1));
                  } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setActive((i) => Math.max(i - 1, 0));
                  } else if (event.key === "Enter") go(results[active]);
                }}
                placeholder="A page, or what a setting does: budget, retention, signing…"
                aria-label="Search settings"
                className="flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-neutral-500"
              />
            </div>
            <ul role="listbox" className="max-h-[50vh] overflow-y-auto p-1.5">
              {results.length === 0 && <li className="px-3 py-6 text-center text-xs text-neutral-500">Nothing matches “{query}”.</li>}
              {results.map((result, index) => (
                <li key={result.id} role="option" aria-selected={index === active}>
                  <button
                    type="button"
                    onMouseEnter={() => setActive(index)}
                    onClick={() => go(result)}
                    className={`flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left ${index === active ? "bg-surface-elevated" : ""}`}
                  >
                    <span className="mt-0.5 rounded border border-surface-border px-1.5 text-[10px] uppercase text-neutral-500">
                      {result.kind === "page" ? "Page" : "Setting"}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-foreground">{result.title}</span>
                      <span className="block truncate text-[11px] text-neutral-500">{result.detail}</span>
                    </span>
                    {index === active && <CornerDownLeft className="mt-1 h-3.5 w-3.5 text-neutral-500" aria-hidden="true" />}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </>
  );
};
