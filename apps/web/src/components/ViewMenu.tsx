"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Brain, ChevronDown, FileText, History, LayoutPanelLeft, Settings } from "lucide-react";
import { ThemeToggle } from "./Theme/ThemeToggle";

interface ViewMenuProps {
  artifactCount: number;
  onMemory: () => void;
  onTurns: () => void;
  onArtifacts: () => void;
}

/** The session's secondary views, named, in one menu instead of a row of unlabeled icons. */
export const ViewMenu: React.FC<ViewMenuProps> = ({ artifactCount, onMemory, onTurns, onArtifacts }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

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

  const item =
    "flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-foreground hover:bg-surface-elevated focus-visible:bg-surface-elevated focus-visible:outline-none";
  const choose = (action: () => void) => () => {
    action();
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        data-testid="view-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-surface-border bg-surface-card px-2.5 text-xs text-foreground transition-colors hover:bg-surface-elevated"
      >
        <LayoutPanelLeft className="h-4 w-4" aria-hidden="true" />
        <span className="hidden sm:inline">View</span>
        <ChevronDown className="h-3 w-3 text-neutral-500" aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-40 mt-1.5 w-56 overflow-hidden rounded-xl border border-surface-border bg-surface-base py-1 shadow-xl">
          <button role="menuitem" type="button" data-testid="artifacts-drawer-toggle" className={item} onClick={choose(onArtifacts)}>
            <FileText className="h-4 w-4 text-orange-500" aria-hidden="true" />
            <span className="flex-1">Artifacts</span>
            {artifactCount > 0 && <span className="rounded-full bg-brand/15 px-1.5 text-[10px] font-semibold text-brand">{artifactCount}</span>}
          </button>
          <button role="menuitem" type="button" data-testid="turn-inspector-toggle" className={item} onClick={choose(onTurns)}>
            <History className="h-4 w-4" aria-hidden="true" />
            <span className="flex-1">Turn history</span>
          </button>
          <button role="menuitem" type="button" data-testid="memory-inspector-trigger" className={item} onClick={choose(onMemory)}>
            <Brain className="h-4 w-4" aria-hidden="true" />
            <span className="flex-1">Memory</span>
          </button>
          <div className="my-1 border-t border-surface-border" />
          <div className="flex items-center justify-between px-3 py-1.5 text-xs text-neutral-600 dark:text-neutral-300">
            <span>Theme</span>
            <ThemeToggle />
          </div>
          <Link role="menuitem" href="/settings" data-testid="view-menu-settings" onClick={() => setOpen(false)} className={item}>
            <Settings className="h-4 w-4" aria-hidden="true" />
            <span className="flex-1">Settings</span>
          </Link>
        </div>
      )}
    </div>
  );
};
