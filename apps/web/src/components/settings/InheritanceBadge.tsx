"use client";

import React from "react";
import { Bot, Building2, Lock, MessageSquare, RotateCcw, Server, User, Users } from "lucide-react";
import { useTeams } from "../../lib/teams";
import { Badge } from "../ui/Badge";

/** Where a setting's value comes from, as the API reports it (`provenance`). */
export interface Provenance {
  source: string;
  locked_by: "org" | "instance" | null;
  refused?: string[];
}

interface InheritanceBadgeProps {
  provenance: Provenance;
  /** Shown on a member's own value, or a team's in its own form: back to the organization's. */
  onReset?: () => void;
  /** Names for `agent:<id>` / `slack_bot:<id>` sources. */
  presetNames?: Record<string, string>;
  className?: string;
}

function presetLabel(source: string, names: Record<string, string> | undefined): string {
  const [kind, id = ""] = source.split(":", 2);
  const name = names?.[source] ?? id;
  if (kind === "agent") return `From agent "${name}"`;
  if (kind === "slack_bot") return `From Slack bot "${name}"`;
  if (kind === "session") return "This session";
  return source;
}

/** Shared by every reset button below: `hover:bg-current/10` tints with each badge's own tone. */
const ResetButton: React.FC<{ onReset: () => void }> = ({ onReset }) => (
  <button
    type="button"
    onClick={(e) => {
      e.preventDefault();
      e.stopPropagation();
      onReset();
    }}
    className="rounded p-0.5 transition-colors hover:bg-current/10"
    aria-label="Use the organization's value"
    title="Use the organization's value"
  >
    <RotateCcw className="h-2.5 w-2.5" aria-hidden="true" />
  </button>
);

/** `From team "<name>"`: the value a team set for its agents' turns. */
const TeamBadge: React.FC<{ teamId: string; onReset?: () => void; className?: string }> = ({ teamId, onReset, className }) => {
  const { teams } = useTeams();
  const name = teams.find((t) => t.id === teamId)?.name ?? teamId;
  return (
    <Badge
      tone="info"
      shape="pill"
      data-provenance="team"
      title="The team's value, for the turns of its agents"
      className={className}
    >
      <Users className="h-2.5 w-2.5" aria-hidden="true" />
      From team &quot;{name}&quot;
      {onReset && <ResetButton onReset={onReset} />}
    </Badge>
  );
};

/** Default / From organization / From team "X" / Yours / Locked by admin / Set by operator / From agent "X". */
export const InheritanceBadge: React.FC<InheritanceBadgeProps> = ({
  provenance,
  onReset,
  presetNames,
  className = "",
}) => {
  const { source, locked_by: lockedBy } = provenance;
  if (lockedBy === "instance" || (source === "instance" && !lockedBy)) {
    return (
      <Badge tone="neutral" shape="pill" data-provenance="instance" title="Set by the operator in the deployment's environment" className={className}>
        <Server className="h-2.5 w-2.5" aria-hidden="true" />
        Set by operator
      </Badge>
    );
  }
  if (lockedBy === "org") {
    return (
      <Badge tone="neutral" shape="pill" data-provenance="locked" title="An administrator locked this setting for everyone" className={className}>
        <Lock className="h-2.5 w-2.5" aria-hidden="true" />
        Locked by admin
      </Badge>
    );
  }
  if (source === "member") {
    return (
      <Badge tone="brand" shape="pill" data-provenance="member" title="Your own value" className={className}>
        <User className="h-2.5 w-2.5" aria-hidden="true" />
        Yours
        {onReset && <ResetButton onReset={onReset} />}
      </Badge>
    );
  }
  if (source === "org") {
    return (
      <Badge tone="success" shape="pill" data-provenance="org" title="The organization's value" className={className}>
        <Building2 className="h-2.5 w-2.5" aria-hidden="true" />
        From organization
      </Badge>
    );
  }
  if (source.startsWith("team:")) {
    return <TeamBadge teamId={source.slice(5)} onReset={onReset} className={className} />;
  }
  if (source.startsWith("agent:") || source.startsWith("slack_bot:") || source === "session") {
    const Icon = source.startsWith("slack_bot:") ? MessageSquare : Bot;
    return (
      <Badge tone="brand" shape="pill" data-provenance="preset" title="Chosen at session level, within the organization's policy" className={className}>
        <Icon className="h-2.5 w-2.5" aria-hidden="true" />
        {presetLabel(source, presetNames)}
      </Badge>
    );
  }
  return (
    <Badge tone="neutral" shape="pill" data-provenance="default" title="The platform's default" className={className}>
      Default
    </Badge>
  );
};
