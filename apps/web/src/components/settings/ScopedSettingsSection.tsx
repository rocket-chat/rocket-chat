"use client";

import React from "react";
import { User, Building2, Lock, ShieldCheck } from "lucide-react";
import { useSession } from "../AuthProvider";
import { Badge } from "../ui/Badge";

interface ScopedSectionProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export const UserScopeSection: React.FC<ScopedSectionProps> = ({
  title,
  description,
  action,
  children,
  className = "",
}) => {
  return (
    <section className={`space-y-4 ${className}`}>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-border/60">
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-brand/10 border border-brand/20 text-brand">
            <User className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-display text-base font-semibold text-foreground">
                {title}
              </h2>
              <Badge tone="brand">Personal scope</Badge>
            </div>
            {description && (
              <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">
                {description}
              </p>
            )}
          </div>
        </div>
        {action && <div className="flex items-center gap-2">{action}</div>}
      </div>

      <div className="pt-1">{children}</div>
    </section>
  );
};

export const ScopeDivider: React.FC<{ label?: string }> = ({
  label = "Organization Configuration",
}) => {
  return (
    <div className="relative my-8 py-2">
      <div className="absolute inset-0 flex items-center" aria-hidden="true">
        <div className="w-full border-t border-dashed border-border/80" />
      </div>
      <div className="relative flex justify-center">
        <span className="bg-surface px-3 py-1 text-[10px] font-mono uppercase tracking-widest text-muted-foreground border border-border/60 rounded-full shadow-sm">
          {label}
        </span>
      </div>
    </div>
  );
};

interface OrgScopeSectionProps extends ScopedSectionProps {
  orgName?: string;
}

export const OrgScopeSection: React.FC<OrgScopeSectionProps> = ({
  title,
  description,
  orgName,
  action,
  children,
  className = "",
}) => {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");
  const tenantOrg = orgName || (session?.user as unknown as { orgId?: string })?.orgId || "Organization";

  return (
    <section className={`space-y-4 ${className}`}>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-border/60">
        <div className="flex items-center gap-2.5">
          <div
            className={`p-1.5 rounded-lg border ${
              isAdmin
                ? "bg-cyan/10 border-cyan/20 text-cyan"
                : "bg-surface-elevated border-border text-muted-foreground"
            }`}
          >
            <Building2 className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-display text-base font-semibold text-foreground">
                {title}
              </h2>
              {isAdmin ? (
                <Badge tone="success">
                  <ShieldCheck className="h-2.5 w-2.5" aria-hidden="true" />
                  Admin editable · {tenantOrg}
                </Badge>
              ) : (
                <Badge>
                  <Lock className="h-2.5 w-2.5" aria-hidden="true" />
                  Read-only · {tenantOrg}
                </Badge>
              )}
            </div>
            {description && (
              <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">
                {description}
              </p>
            )}
          </div>
        </div>
        {isAdmin && action && <div className="flex items-center gap-2">{action}</div>}
      </div>

      {!isAdmin && (
        <div className="flex items-center gap-2 p-2.5 rounded-lg bg-surface-card/60 border border-border/70 text-neutral-600 dark:text-neutral-400 text-sm">
          <Lock className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
          <span>
            Organization-wide settings are enforced by administrators. Contact your workspace admin to request changes.
          </span>
        </div>
      )}

      <div
        className={`pt-1 transition-opacity duration-200 ${
          !isAdmin ? "opacity-75" : ""
        }`}
      >
        {children}
      </div>
    </section>
  );
};
