"use client";

import React from "react";
import type { LucideIcon } from "lucide-react";

/** One choice shown as a card with an icon: a trigger, a mode, a delivery. */
export const Tile: React.FC<{
  icon: LucideIcon;
  label: string;
  hint?: string;
  selected: boolean;
  onClick: () => void;
  testId: string;
  /** `radio` inside a one-of group, `checkbox` for an on/off choice. */
  role?: "radio" | "checkbox";
  disabled?: boolean;
}> = ({ icon: Icon, label, hint, selected, onClick, testId, role = "radio", disabled = false }) => (
  <button
    type="button"
    role={role}
    aria-checked={selected}
    data-testid={testId}
    disabled={disabled}
    onClick={onClick}
    className={`flex min-h-[4.5rem] flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors disabled:cursor-default ${
      selected
        ? "border-brand bg-brand/10 text-foreground"
        : "border-surface-border bg-surface-card text-neutral-700 hover:border-neutral-400 dark:text-neutral-300"
    }`}
  >
    <Icon className={`h-4 w-4 ${selected ? "text-brand" : "text-neutral-500"}`} aria-hidden="true" />
    <span className="text-xs font-medium">{label}</span>
    {hint && (
      <span title={hint} className="line-clamp-3 text-[11px] leading-snug text-neutral-600 dark:text-neutral-400">
        {hint}
      </span>
    )}
  </button>
);

/** A labelled row of tiles that behave as one choice. */
export const TileGroup: React.FC<{ label: string; children: React.ReactNode; role?: "radiogroup" | "group" }> = ({
  label,
  children,
  role = "radiogroup",
}) => (
  <div role={role} aria-label={label} className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
    {children}
  </div>
);
