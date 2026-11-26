"use client";

import React from "react";
import { AlertTriangle, GitBranch } from "lucide-react";
import { Badge } from "../ui/Badge";
import type { SkillSource } from "../../types/mission";

/** A synchronised skill's source, as a pill linking to its folder on GitHub. */
export const SkillSourceBadge: React.FC<{ source: SkillSource }> = ({ source }) => (
  <a
    href={source.url}
    target="_blank"
    rel="noreferrer"
    data-testid="skill-source-badge"
    data-status={source.status}
    title={`Synchronised with ${source.repository} (${source.ref}), folder ${source.path}`}
    onClick={(e) => e.stopPropagation()}
    className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
  >
    <Badge tone={source.status === "synced" ? "info" : "warning"}>
      <GitBranch className="h-3 w-3" aria-hidden="true" />
      {source.name}
      {source.plugin ? ` · ${source.plugin}` : ""}
    </Badge>
  </a>
);

/** Why a synchronised skill is not in step with its repository, and what to do about it. */
export const SkillSourceNotice: React.FC<{ source: SkillSource }> = ({ source }) => {
  if (source.status === "synced" && !source.problem) return null;
  const missing = source.status === "missing";
  return (
    <p
      data-testid="skill-source-notice"
      className="flex items-start gap-1.5 text-[12px] text-amber-700 dark:text-amber-400"
    >
      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
      <span>
        {missing
          ? `Out of sync: this skill is no longer in ${source.repository} (${source.ref}), so it was deactivated. Delete it if it is no longer needed.`
          : source.problem}
      </span>
    </p>
  );
};
