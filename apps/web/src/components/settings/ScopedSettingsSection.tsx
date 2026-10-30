"use client";

import React from "react";
import { Lock } from "lucide-react";
import { Badge } from "../ui/Badge";
import { useAccess } from "../../lib/access";

interface ScopedSectionProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

const SectionHeader: React.FC<{
  title: string;
  description?: string;
  badge?: React.ReactNode;
  action?: React.ReactNode;
}> = ({ title, description, badge, action }) => (
  // Wraps instead of squeezing: the settings column is narrower than the window (the navigation
  // takes a share of it), so the action drops below the title when there is no room beside it.
  <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
    <div className="min-w-0 flex-1 basis-72">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h2 className="font-display text-base font-semibold text-foreground">{title}</h2>
        {badge}
      </div>
      {description && (
        <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">{description}</p>
      )}
    </div>
    {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
  </div>
);

export const UserScopeSection: React.FC<ScopedSectionProps> = ({
  title,
  description,
  action,
  children,
  className = "",
}) => (
  <section className={`space-y-4 ${className}`}>
    <SectionHeader title={title} description={description} action={action} />
    <div>{children}</div>
  </section>
);

/** Separates the personal part of a page from the organization part. */
export const ScopeDivider: React.FC<{ label?: string }> = ({
  label = "Organization Configuration",
}) => (
  <div role="separator" aria-label={label} className="my-8 border-t border-border/70" />
);

interface OrgScopeSectionProps extends ScopedSectionProps {
  orgName?: string;
}

export const OrgScopeSection: React.FC<OrgScopeSectionProps> = ({
  title,
  description,
  action,
  children,
  className = "",
}) => {
  const { isAdmin } = useAccess();

  return (
    <section className={`space-y-4 ${className}`}>
      <SectionHeader
        title={title}
        description={description}
        badge={
          // Shown to administrators too: a change here applies to every member.
          <Badge className="whitespace-nowrap">
            <Lock className="h-2.5 w-2.5" aria-hidden="true" />
            {isAdmin ? "Admin only · whole organization" : "Read-only"}
          </Badge>
        }
        action={isAdmin ? action : undefined}
      />

      {!isAdmin && (
        <p className="flex items-center gap-2 rounded-lg border border-border/70 bg-surface-card/60 p-2.5 text-sm text-neutral-600 dark:text-neutral-400">
          <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          You can use these; administrators manage them for the whole organization.
        </p>
      )}

      {/* Members see the organization's servers, skills and collections they can use as they are:
          read-only, not dimmed. Pages that only administrators change are hidden from the nav. */}
      <div>{children}</div>
    </section>
  );
};
