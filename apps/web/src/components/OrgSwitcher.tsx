"use client";

import React, { useEffect, useState } from "react";
import { Building2, Check, ChevronsUpDown } from "lucide-react";
import { refreshAccess, useAccess } from "../lib/access";

interface Organization {
  id: string;
  name: string;
  role: string;
  active: boolean;
}

/** The active organization, and a menu to switch to another one the person belongs to. */
export const OrgSwitcher: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const access = useAccess();
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    fetch("/v1/me/organizations")
      .then((res) => (res.ok ? res.json() : { organizations: [] }))
      .then((body: { organizations: Organization[] }) => setOrgs(body.organizations ?? []))
      .catch(() => setOrgs([]));
  }, [open]);

  const switchTo = async (orgId: string) => {
    setError(null);
    const res = await fetch("/v1/auth/switch-org", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ org_id: orgId }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "Could not switch organization.");
      return;
    }
    await refreshAccess();
    // Every page holds the previous organization's data: start again from the cockpit.
    window.location.assign("/chat/draft");
  };

  return (
    <div className="relative" data-testid="org-switcher">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg border border-surface-border bg-surface-card/60 px-2.5 py-2 text-left hover:bg-surface-elevated"
      >
        <Building2 className="h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-display text-xs font-medium text-foreground" data-testid="org-name">
            {access.orgName || access.orgId || "Organization"}
          </span>
          {!compact && (
            <span className="text-[10px] capitalize text-neutral-500 dark:text-neutral-400">
              {access.role}
            </span>
          )}
        </span>
        <ChevronsUpDown className="h-3.5 w-3.5 text-neutral-500" aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Organizations"
          className="absolute left-0 right-0 z-50 mt-1 rounded-lg border border-surface-border bg-surface-card p-1 shadow-lg"
        >
          {orgs.length === 0 && (
            <p className="px-2 py-1.5 text-[11px] text-neutral-500">Loading organizations…</p>
          )}
          {orgs.map((org) => (
            <button
              key={org.id}
              type="button"
              role="menuitem"
              disabled={org.active}
              onClick={() => switchTo(org.id)}
              className="flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-surface-elevated disabled:cursor-default"
            >
              <span className="truncate">{org.name}</span>
              <span className="flex items-center gap-1 text-[10px] capitalize text-neutral-500">
                {org.role}
                {org.active && <Check className="h-3 w-3 text-brand" aria-label="Active" />}
              </span>
            </button>
          ))}
          {error && (
            <p role="alert" className="px-2 py-1.5 text-[11px] text-rose-500">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
};
