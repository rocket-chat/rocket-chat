"use client";

import React, { useEffect, useState } from "react";
import { Building2, Check, ChevronsUpDown } from "lucide-react";
import { refreshAccess, useAccess } from "../lib/access";
import { buttonClass } from "./ui/Button";

interface Organization {
  id: string;
  name: string;
  role: string;
  active: boolean;
}

/** The active organization, and a menu to switch to another one the person belongs to: the same
 *  one component everywhere it appears (cockpit sidebar, settings nav), so it never drifts into
 *  two different looks again. */
export const OrgSwitcher: React.FC = () => {
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
        className={buttonClass("flat", "md", {
          fullWidth: true,
          // Two lines (org name + role) need more room than Button's fixed single-line height —
          // but its own text-sm/leading-6 still cascades into any child that doesn't set its own
          // line-height, so both lines below pin theirs explicitly instead of inheriting it.
          className: "!h-auto !justify-start !px-2.5 gap-2 py-1.5 text-left leading-none",
        })}
      >
        <Building2 className="h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate font-display text-xs font-medium leading-tight text-foreground" data-testid="org-name">
            {access.orgName || access.orgId || "Organization"}
          </span>
          <span className="truncate text-[11px] capitalize leading-none text-neutral-500 dark:text-neutral-400">
            {access.role}
          </span>
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
            <p className="px-2 py-1.5 text-[12px] text-neutral-500">Loading organizations…</p>
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
              <span className="flex items-center gap-1 text-[11px] capitalize text-neutral-500">
                {org.role}
                {org.active && <Check className="h-3 w-3 text-brand" aria-label="Active" />}
              </span>
            </button>
          ))}
          {error && (
            <p role="alert" className="px-2 py-1.5 text-[12px] text-rose-500">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
};
