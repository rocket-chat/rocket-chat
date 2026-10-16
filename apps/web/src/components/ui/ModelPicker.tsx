"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { useMissionStore } from "../../lib/store";
import { cn } from "./cn";

const MAX_SHOWN = 200;

interface ModelPickerProps {
  /** The control id a `Field` label points at. */
  id?: string;
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
  /** Offer an empty choice meaning "inherit", labelled with this text. */
  inheritLabel?: string;
  "aria-describedby"?: string;
  "data-testid"?: string;
}

/**
 * One searchable model choice for every form that names a model. It loads the full catalogue
 * (every provider model the platform can reach) the first time it is used, so a form opened
 * outside the chat cockpit lists the same models as the composer.
 */
export function ModelPicker({
  id,
  value,
  onChange,
  disabled,
  inheritLabel,
  "aria-describedby": describedBy,
  "data-testid": testId,
}: ModelPickerProps) {
  const models = useMissionStore((s) => s.availableModels);
  const loaded = useMissionStore((s) => s.modelsLoaded);
  const fetchModels = useMissionStore((s) => s.fetchModels);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = `${id || testId || "model"}-options`;

  useEffect(() => {
    if (!loaded) fetchModels();
  }, [loaded, fetchModels]);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    const close = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const matches = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return models.filter((m) => {
      const text = `${m.name} ${m.id} ${m.provider}`.toLowerCase();
      return words.every((w) => text.includes(w));
    });
  }, [models, query]);

  const current = models.find((m) => m.id === value);
  const label = value ? current?.name || value : inheritLabel || "Choose a model";

  const choose = (model: string) => {
    onChange(model);
    setOpen(false);
    setQuery("");
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        id={id}
        data-testid={testId}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-describedby={describedBy}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-surface-border bg-surface-card px-3 py-2 text-left text-sm text-foreground hover:border-neutral-400 focus:outline-none focus:ring-2 focus:ring-brand/20 disabled:opacity-60 dark:hover:border-neutral-600"
      >
        <span className="min-w-0 truncate">
          {label}
          {value && <span className="ml-2 font-mono text-xs text-neutral-500">{value}</span>}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-neutral-500" aria-hidden="true" />
      </button>

      {open && (
        <div
          className="absolute left-0 right-0 z-50 mt-1 rounded-xl border border-surface-border bg-surface-card p-2 shadow-xl"
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        >
          <div className="relative mb-2">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-neutral-500" aria-hidden="true" />
            <input
              ref={searchRef}
              type="search"
              aria-label="Search models"
              data-testid={testId ? `${testId}-search` : undefined}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={loaded ? `Search ${models.length} models (claude, gpt, deepseek…)` : "Loading models…"}
              className="w-full rounded-lg border border-surface-border bg-surface-base py-2 pl-8 pr-2 text-xs text-foreground placeholder:text-neutral-500 focus:border-brand/60 focus:outline-none"
            />
          </div>
          <ul id={listId} role="listbox" aria-label="Models" className="max-h-72 overflow-y-auto">
            {inheritLabel && !query && (
              <Option selected={!value} onChoose={() => choose("")}>
                <span className="font-medium">{inheritLabel}</span>
              </Option>
            )}
            {matches.slice(0, MAX_SHOWN).map((m) => (
              <Option key={m.id} selected={m.id === value} onChoose={() => choose(m.id)}>
                <span className="font-medium">{m.name}</span>
                <span className="ml-2 font-mono text-[11px] text-neutral-500">{m.id}</span>
              </Option>
            ))}
            {matches.length > MAX_SHOWN && (
              <li className="px-2 py-1.5 text-[11px] text-neutral-500">
                {matches.length - MAX_SHOWN} more: refine the search.
              </li>
            )}
            {matches.length === 0 && (
              <li className="px-2 py-3 text-center text-xs text-neutral-500">
                {loaded ? `No model matches "${query}".` : "Loading models…"}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

function Option({
  selected,
  onChoose,
  children,
}: {
  selected: boolean;
  onChoose: () => void;
  children: React.ReactNode;
}) {
  return (
    <li role="option" aria-selected={selected}>
      <button
        type="button"
        onClick={onChoose}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-xs",
          selected ? "bg-brand/10 text-brand" : "text-foreground hover:bg-surface-elevated",
        )}
      >
        <span className="min-w-0 truncate">{children}</span>
        {selected && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
      </button>
    </li>
  );
}
