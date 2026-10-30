"use client";

import React from "react";
import { Bot, Building2, Lock, MessageSquare, RotateCcw, Server, User } from "lucide-react";

/** Where a setting's value comes from, as the API reports it (`provenance`). */
export interface Provenance {
  source: string;
  locked_by: "org" | "instance" | null;
  refused?: string[];
}

interface InheritanceBadgeProps {
  provenance: Provenance;
  /** Shown on a member's own value: back to the organization's. */
  onReset?: () => void;
  /** Names for `agent:<id>` / `slack_bot:<id>` sources. */
  presetNames?: Record<string, string>;
  className?: string;
}

const base =
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium select-none";

function presetLabel(source: string, names: Record<string, string> | undefined): string {
  const [kind, id = ""] = source.split(":", 2);
  const name = names?.[source] ?? id;
  if (kind === "agent") return `From agent "${name}"`;
  if (kind === "slack_bot") return `From Slack bot "${name}"`;
  if (kind === "session") return "This session";
  return source;
}

/** Default / From organization / Yours / Locked by admin / Set by operator / From agent "X". */
export const InheritanceBadge: React.FC<InheritanceBadgeProps> = ({
  provenance,
  onReset,
  presetNames,
  className = "",
}) => {
  const { source, locked_by: lockedBy } = provenance;
  if (lockedBy === "instance" || (source === "instance" && !lockedBy)) {
    return (
      <span
        data-provenance="instance"
        className={`${base} border-border bg-secondary text-muted-foreground ${className}`}
        title="Set by the operator in the deployment's environment"
      >
        <Server className="h-2.5 w-2.5" aria-hidden="true" />
        Set by operator
      </span>
    );
  }
  if (lockedBy === "org") {
    return (
      <span
        data-provenance="locked"
        className={`${base} border-border bg-secondary text-muted-foreground ${className}`}
        title="An administrator locked this setting for everyone"
      >
        <Lock className="h-2.5 w-2.5" aria-hidden="true" />
        Locked by admin
      </span>
    );
  }
  if (source === "member") {
    return (
      <span
        data-provenance="member"
        className={`${base} border-primary/30 bg-primary/10 text-primary ${className}`}
        title="Your own value"
      >
        <User className="h-2.5 w-2.5" aria-hidden="true" />
        Yours
        {onReset && (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onReset();
            }}
            className="rounded p-0.5 transition-colors hover:bg-primary/20"
            aria-label="Use the organization's value"
            title="Use the organization's value"
          >
            <RotateCcw className="h-2.5 w-2.5" aria-hidden="true" />
          </button>
        )}
      </span>
    );
  }
  if (source === "org") {
    return (
      <span
        data-provenance="org"
        className={`${base} border-emerald-600/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 ${className}`}
        title="The organization's value"
      >
        <Building2 className="h-2.5 w-2.5" aria-hidden="true" />
        From organization
      </span>
    );
  }
  if (source.startsWith("agent:") || source.startsWith("slack_bot:") || source === "session") {
    const Icon = source.startsWith("slack_bot:") ? MessageSquare : Bot;
    return (
      <span
        data-provenance="preset"
        className={`${base} border-brand/30 bg-brand/10 text-brand ${className}`}
        title="Chosen at session level, within the organization's policy"
      >
        <Icon className="h-2.5 w-2.5" aria-hidden="true" />
        {presetLabel(source, presetNames)}
      </span>
    );
  }
  return (
    <span
      data-provenance="default"
      className={`${base} border-border bg-surface-card text-muted-foreground ${className}`}
      title="The platform's default"
    >
      Default
    </span>
  );
};
