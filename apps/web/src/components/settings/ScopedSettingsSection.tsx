"use client";

import React from "react";
import { Lock } from "lucide-react";
import { useSession } from "../AuthProvider";
import { Badge } from "../ui/Badge";

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
  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div className="min-w-0">
      <div className="flex items-center gap-2">
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
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  return (
    <section className={`space-y-4 ${className}`}>
      <SectionHeader
        title={title}
        description={description}
        badge={
          isAdmin ? undefined : (
            <Badge>
              <Lock className="h-2.5 w-2.5" aria-hidden="true" />
              Read-only
            </Badge>
          )
        }
        action={isAdmin ? action : undefined}
      />

      {!isAdmin && (
        <p className="flex items-center gap-2 rounded-lg border border-border/70 bg-surface-card/60 p-2.5 text-sm text-neutral-600 dark:text-neutral-400">
          <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          Organization-wide settings are managed by administrators. Contact your workspace admin to request changes.
        </p>
      )}

      <div className={!isAdmin ? "opacity-75" : undefined}>{children}</div>
    </section>
  );
};
