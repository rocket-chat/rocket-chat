"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ShieldCheck,
  Bot,
  Server,
  Code2,
  Cpu,
  Database,
  GitPullRequest,
  MessageSquare,
  User,
  Coins,
  Box,
  ArrowLeft,
  Building2,
  Users,
  X,
} from "lucide-react";

import { useSession } from "../AuthProvider";
import { useMissionStore } from "../../lib/store";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  requiresAdmin?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    href: "/settings/user/profile",
    label: "Profile & Account",
    icon: User,
  },
  {
    href: "/settings/agents",
    label: "Custom Agents",
    icon: Bot,
  },
  {
    href: "/settings/subagents",
    label: "Subagents & Delegation",
    icon: Cpu,
  },
  {
    href: "/settings/mcp",
    label: "MCP Servers",
    icon: Server,
  },
  {
    href: "/settings/skills",
    label: "Skills & Tools",
    icon: Code2,
  },
  {
    href: "/settings/knowledge",
    label: "Knowledge Bases",
    icon: Database,
  },
  {
    href: "/settings/github",
    label: "Git & Version Control",
    icon: GitPullRequest,
  },
  {
    href: "/settings/slack",
    label: "Slack & Chat Ops",
    icon: MessageSquare,
  },
  {
    href: "/settings/org/users",
    label: "Users & Roles",
    icon: Users,
    requiresAdmin: true,
  },
  {
    href: "/settings/org/usage",
    label: "Token Usage & Spend",
    icon: Coins,
  },
  {
    href: "/settings/org/sandboxes",
    label: "Sandbox Fleet & Images",
    icon: Box,
    requiresAdmin: true,
  },
  {
    href: "/settings/org/models",
    label: "Models & Routing",
    icon: Cpu,
    requiresAdmin: true,
  },
  {
    href: "/settings/org/governance",
    label: "Governance & Memory",
    icon: ShieldCheck,
    requiresAdmin: true,
  },
  {
    href: "/settings/org",
    label: "General",
    icon: ShieldCheck,
    requiresAdmin: true,
  },
];

export const SettingsNav: React.FC = () => {
  const pathname = usePathname();
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");
  const tenantOrg = (session?.user as unknown as { orgId?: string })?.orgId || "default_org";

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

          {/* Console Scope Banner */}
          <div className="px-2.5 py-2 rounded-lg bg-surface-card/60 border border-surface-border flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Building2 className="w-4 h-4 text-brand" />
              <div className="flex flex-col">
                <span className="text-xs font-display font-medium text-foreground leading-tight">
                  {tenantOrg}
                </span>
                <span className="text-[10px] text-neutral-500 dark:text-neutral-400">
                  {isAdmin ? "Admin Access" : "Member"}
                </span>
              </div>
            </div>
            <span className="px-1.5 py-0.5 text-[9px] rounded-lg bg-brand/10 text-brand border border-brand/20">
              {isAdmin ? "ADMIN" : "USER"}
            </span>
          </div>

          {/* Settings Navigation */}
          <div>
            <div className="px-2.5 pb-2 flex items-center justify-between text-[11px] font-semibold text-neutral-500 dark:text-neutral-400">
              <span>Settings</span>
            </div>
            <nav className="space-y-0.5">
              {NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href;
                const isLocked = item.requiresAdmin && !isAdmin;

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={handleNavClick}
                    className={`flex items-center justify-between px-2.5 py-2 text-xs rounded-lg transition-colors group ${
                      isActive
                        ? "bg-surface-elevated text-foreground font-semibold border-l-2 border-brand shadow-sm"
                        : isLocked
                        ? "text-neutral-400 dark:text-neutral-600 hover:text-neutral-500 cursor-not-allowed opacity-75"
                        : "text-neutral-600 hover:text-foreground hover:bg-surface-elevated/60 dark:text-neutral-400 dark:hover:text-white"
                    }`}
                  >
                    <span className="flex items-center gap-2.5">
                      <Icon
                        className={`w-4 h-4 ${
                          isActive
                            ? "text-brand"
                            : isLocked
                            ? "text-neutral-400 dark:text-neutral-600"
                            : "text-neutral-400 dark:text-neutral-500 group-hover:text-brand"
                        }`}
                      />
                      <span>{item.label}</span>
                    </span>
                    {/* Marked for administrators too: what they change here applies to everyone. */}
                    {item.requiresAdmin && (
                      <span
                        data-testid="nav-admin-only"
                        title="Administrators only: applies to the whole organization"
                        className="text-[9px] px-1.5 py-0.5 rounded-lg bg-surface-card border border-surface-border text-neutral-700 dark:text-neutral-300"
                      >
                        ADMIN
                      </span>
                    )}
                  </Link>
                );
              })}
            </nav>
          </div>
        </div>

        {/* Subnav Footer Info */}
        <div className="p-3 border-t border-surface-border bg-surface-sidebar/50">
          <div className="flex items-center justify-between text-[11px] text-neutral-500 dark:text-neutral-400">
            <span>Tenant Scope: {tenantOrg}</span>
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
          </div>
        </div>
      </aside>
    </>
  );
};
