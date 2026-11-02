"use client";

import React from "react";
import { Lock } from "lucide-react";
import type { Team } from "../../lib/teams";

/** What a page of keys or limits says to someone with nothing here to manage. */
export const NotManaged: React.FC<{ team: Team | null; what: string }> = ({ team, what }) => (
  <p data-testid="not-managed" className="flex items-center gap-2 rounded-lg border border-border/70 bg-surface-card/60 p-3 text-sm text-neutral-600 dark:text-neutral-400">
    <Lock className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
    {team
      ? `The ${what} of the team ${team.name} are managed by its administrators.`
      : `The organization's ${what} are managed by its administrators, and a team's by the team's.`}
  </p>
);
