"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, X } from "lucide-react";

import { AppVersion } from "./AppVersion";
import { useMissionStore } from "../../lib/store";
import { SettingsSearchButton } from "./SettingsSearch";
import { NAV_SECTIONS, type NavItem, type NavOwner } from "./nav-data";
import { OwnerFilterControl } from "./OwnerFilterControl";
import { OrgSwitcher } from "../OrgSwitcher";
import { hasCapability, type Access } from "../../lib/access";
import { useAccess } from "../../lib/access";
import { useTeams, type Team } from "../../lib/teams";
import { useOwnerFilter, useOwnerFilterSync } from "../../lib/useOwnerFilter";
import type { OwnerFilter } from "../../lib/ownership";

export const SettingsNav: React.FC = () => {
  const pathname = usePathname();
  const access = useAccess();
  const { teams } = useTeams();
  useOwnerFilterSync();
  const [owner] = useOwnerFilter();
  const sections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => visible(item, access, teams) && followsOwner(item, owner)),
  })).filter((section) => section.items.length > 0);
  // The filter travels with the links, so moving between pages keeps the owner in view.
  const hrefOf = (item: NavItem) => (owner !== "all" && item.owners ? `${item.href}?owner=${encodeURIComponent(owner)}` : item.href);

  const missionSession = useMissionStore((s) => s.session);
  const isSettingsNavOpen = useMissionStore((s) => s.isSettingsNavOpen);
  const setIsSettingsNavOpen = useMissionStore((s) => s.setIsSettingsNavOpen);

  const backHref =
    missionSession?.session_id &&
    !missionSession.is_draft &&
    missionSession.session_id !== "draft" &&
    missionSession.session_id !== "new"
      ? `/chat/${missionSession.session_id}`
      : "/chat/draft";

  const handleNavClick = () => {
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setIsSettingsNavOpen(false);
    }
  };

  return (
    <>
      {/* Mobile Backdrop */}
      {isSettingsNavOpen && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 md:hidden"
          onClick={() => setIsSettingsNavOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={`${
          isSettingsNavOpen
            ? "fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] shadow-2xl flex"
            : "hidden md:flex"
        } md:relative md:w-72 md:inset-auto md:z-auto flex-shrink-0 bg-surface-sidebar border-r border-surface-border flex-col justify-between overflow-y-auto select-none transition-all duration-200`}
      >
        <div className="p-3.5 space-y-5">
          {/* Header row with back button and mobile close button */}
          <div className="flex items-center gap-2">
            <Link
              href={backHref}
              onClick={handleNavClick}
              className="flex-1 flex items-center gap-2 px-3 py-2 rounded-lg border border-surface-border hover:border-brand/40 bg-surface-card/60 hover:bg-surface-elevated text-xs text-neutral-600 hover:text-foreground dark:text-neutral-300 dark:hover:text-white transition-all shadow-sm group"
            >
              <ArrowLeft className="w-4 h-4 text-brand group-hover:-translate-x-0.5 transition-transform" />
              <span>Back to the cockpit</span>
            </Link>
            <button
              type="button"
              onClick={() => setIsSettingsNavOpen(false)}
              className="p-2 rounded-lg hover:bg-surface-elevated text-neutral-400 hover:text-foreground md:hidden cursor-pointer"
              aria-label="Close Settings Navigation"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <OrgSwitcher />

          <SettingsSearchButton />

          <OwnerFilterControl />

          {sections.map((section) => (
            <div key={section.title}>
              <div className="flex items-baseline justify-between px-2.5 pb-1.5">
                <span className="text-[11px] font-semibold text-neutral-600 dark:text-neutral-300">{section.title}</span>
                <span className="text-[10px] text-neutral-500">{section.hint}</span>
              </div>
              <nav className="space-y-0.5" aria-label={section.title}>
                {section.items.map((item) => {
                  const Icon = item.icon;
                  const isActive = pathname === item.href;
                  return (
                    <Link
                      key={item.href}
                      href={hrefOf(item)}
                      onClick={handleNavClick}
                      aria-current={isActive ? "page" : undefined}
                      className={`group flex items-center justify-between rounded-lg px-2.5 py-2 text-xs transition-colors ${
                        isActive
                          ? "border-l-2 border-brand bg-surface-elevated font-semibold text-foreground shadow-sm"
                          : "text-neutral-700 hover:bg-surface-elevated/60 hover:text-foreground dark:text-neutral-300"
                      }`}
                    >
                      <span className="flex items-center gap-2.5">
                        <Icon className={`h-4 w-4 ${isActive ? "text-brand" : "text-neutral-500 group-hover:text-brand"}`} />
                        <span>{item.label}</span>
                      </span>
                      {/* Marked for administrators too: what they change here applies to everyone. */}
                      {item.requiresAdmin && access.isAdmin && (
                        <span
                          data-testid="nav-admin-only"
                          title="Administrators only: applies to the whole organization"
                          className="rounded-lg border border-surface-border bg-surface-card px-1.5 py-0.5 text-[9px] text-neutral-700 dark:text-neutral-300"
                        >
                          ADMIN
                        </span>
                      )}
                    </Link>
                  );
                })}
              </nav>
            </div>
          ))}
        </div>

        <div className="p-3 border-t border-surface-border bg-surface-sidebar/50 text-[11px] text-neutral-500 dark:text-neutral-400">
          Rocket Chat <AppVersion />
        </div>
      </aside>
    </>
  );
};

/** Admin-only pages are hidden from everyone else (a team page from those in no team), and
 * capability pages from roles without it. */
export function visible(item: NavItem, access: Access, teams: Team[] = []): boolean {
  if (!access.loaded) return !item.requiresAdmin && !item.capability;
  const teamMember = item.orTeamMember && teams.some((t) => t.my_role !== null);
  if (item.requiresAdmin && !access.isAdmin && !teamMember) return false;
  return !item.capability || hasCapability(access, item.capability);
}

/** Whether a page is about the owner the filter names (pages that follow no owner always are). */
export function followsOwner(item: NavItem, owner: OwnerFilter): boolean {
  if (owner === "all" || !item.owners) return true;
  const kind: NavOwner = owner === "me" || owner === "org" ? owner : "team";
  return item.owners.includes(kind);
}
