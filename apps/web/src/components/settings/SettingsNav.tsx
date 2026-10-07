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
  Key,
  AtSign,
  Gauge,
  Bell,
  ArrowLeft,
  Building2,
  Coins,
  Box,
} from "lucide-react";

import { useSession } from "next-auth/react";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
  badgeColor?: string;
  requiresAdmin?: boolean;
}

const ORG_NAV_ITEMS: NavItem[] = [
  {
    href: "/settings/org",
    label: "General & Compliance",
    icon: ShieldCheck,
    badge: "SOC2",
    requiresAdmin: true,
  },
  {
    href: "/settings/agents",
    label: "Custom Agents",
    icon: Bot,
    badge: "1",
  },
  {
    href: "/settings/subagents",
    label: "Subagents & Delegation",
    icon: Cpu,
    badge: "3",
  },
  {
    href: "/settings/mcp",
    label: "MCP Servers",
    icon: Server,
    badge: "3",
  },
  {
    href: "/settings/skills",
    label: "Skills & Tools",
    icon: Code2,
    badge: "Sandbox",
  },
  {
    href: "/settings/knowledge",
    label: "Knowledge Bases (RAG)",
    icon: Database,
    badge: "Synced",
    badgeColor: "text-emerald-400",
  },
  {
    href: "/settings/org/usage",
    label: "Token Usage & Spend",
    icon: Coins,
    badge: "Billing",
    badgeColor: "text-emerald-400",
  },
  {
    href: "/settings/org/sandboxes",
    label: "Sandbox Fleet & Images",
    icon: Box,
    badge: "Fleet",
  },
  {
    href: "/settings/github",
    label: "GitHub Integration",
    icon: GitPullRequest,
    badge: "Active",
    badgeColor: "text-emerald-400",
  },
  {
    href: "/settings/slack",
    label: "Slack & Chat Ops",
    icon: MessageSquare,
    badge: "2 ch",
  },
];

const USER_NAV_ITEMS: NavItem[] = [
  {
    href: "/settings/user/profile",
    label: "Profile & Account",
    icon: User,
  },
  {
    href: "/settings/user/usage",
    label: "My Token Spend",
    icon: Coins,
  },
  {
    href: "/settings/user/git",
    label: "Personal Git Credentials",
    icon: Key,
    badge: "PAT",
  },
  {
    href: "/settings/user/slack",
    label: "Slack Routing & Mentions",
    icon: AtSign,
    badge: "@alex",
  },
  {
    href: "/settings/user/models",
    label: "Model Defaults & Turbomode",
    icon: Gauge,
    badge: "Sonnet",
    badgeColor: "text-brand",
  },
  {
    href: "/settings/user/notifications",
    label: "Notification Channels",
    icon: Bell,
  },
];

export const SettingsNav: React.FC = () => {
  const pathname = usePathname();
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = userRoles.includes("admin") || userRoles.includes("owner");
  const tenantOrg = (session?.user as unknown as { orgId?: string })?.orgId || "default_org";

  return (
    <aside className="w-72 flex-shrink-0 bg-surface-sidebar border-r border-surface-border flex flex-col justify-between overflow-y-auto select-none">
      <div className="p-3.5 space-y-6">
        {/* Back to Cockpit button */}
        <Link
          href="/"
          className="flex items-center gap-2 px-3 py-2 rounded-lg border border-surface-border hover:border-brand/40 bg-surface-card/60 hover:bg-surface-elevated text-xs font-mono text-neutral-600 hover:text-foreground dark:text-neutral-300 dark:hover:text-white transition-all shadow-sm group"
        >
          <ArrowLeft className="w-4 h-4 text-brand group-hover:-translate-x-0.5 transition-transform" />
          <span>RETURN TO COCKPIT</span>
        </Link>

        {/* Console Scope Banner */}
        <div className="px-2.5 py-2 rounded-lg bg-surface-card/60 border border-surface-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Building2 className="w-4 h-4 text-brand" />
            <div className="flex flex-col">
              <span className="text-xs font-display font-medium text-foreground leading-tight">
                {tenantOrg}
              </span>
              <span className="text-[10px] font-mono text-neutral-500 dark:text-neutral-400">
                {isAdmin ? "Admin Access" : "Member"}
              </span>
            </div>
          </div>
          <span className="px-1.5 py-0.5 text-[9px] font-mono uppercase rounded bg-brand/10 text-brand border border-brand/20">
            {isAdmin ? "ADMIN" : "USER"}
          </span>
        </div>

        {/* SECTION A: ORGANIZATION */}
        <div>
          <div className="px-2.5 pb-2 flex items-center justify-between text-[11px] font-mono font-semibold tracking-wider uppercase text-neutral-500 dark:text-neutral-400">
            <span>Organization ({tenantOrg})</span>
            <span className="text-[10px] text-neutral-400 dark:text-neutral-500 font-mono">ORG</span>
          </div>
          <nav className="space-y-0.5">
            {ORG_NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href;
              const isLocked = item.requiresAdmin && !isAdmin;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center justify-between px-2.5 py-2 text-xs rounded-lg transition-colors group ${
                    isActive
                      ? "bg-surface-elevated text-foreground font-semibold border-l-2 border-brand shadow-sm"
                      : isLocked
                      ? "text-neutral-400 dark:text-neutral-600 hover:text-neutral-500 cursor-not-allowed opacity-80"
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
                  {isLocked ? (
                    <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-card border border-surface-border text-neutral-500">
                      ADMIN ONLY
                    </span>
                  ) : item.badge ? (
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-elevated border border-surface-border ${
                        item.badgeColor || "text-neutral-500 dark:text-neutral-400"
                      }`}
                    >
                      {item.badge}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </nav>
        </div>

        {/* SECTION B: USER PREFERENCES */}
        <div>
          <div className="px-2.5 pb-2 flex items-center justify-between text-[11px] font-mono font-semibold tracking-wider uppercase text-neutral-500 dark:text-neutral-400">
            <span>User Preferences</span>
            <span className="text-[10px] text-neutral-400 dark:text-neutral-500 font-mono">ALEX</span>
          </div>
          <nav className="space-y-0.5">
            {USER_NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center justify-between px-2.5 py-2 text-xs rounded-lg transition-colors group ${
                    isActive
                      ? "bg-surface-elevated text-foreground font-semibold border-l-2 border-brand shadow-sm"
                      : "text-neutral-600 hover:text-foreground hover:bg-surface-elevated/60 dark:text-neutral-400 dark:hover:text-white"
                  }`}
                >
                  <span className="flex items-center gap-2.5">
                    <Icon
                      className={`w-4 h-4 ${
                        isActive ? "text-brand" : "text-neutral-500 group-hover:text-neutral-300"
                      }`}
                    />
                    <span>{item.label}</span>
                  </span>
                  {item.badge && (
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-elevated border border-surface-border ${
                        item.badgeColor || "text-neutral-400"
                      }`}
                    >
                      {item.badge}
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
        <div className="flex items-center justify-between text-[11px] font-mono text-neutral-500">
          <span>RLS Enforced: Acme Labs</span>
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
        </div>
      </div>
    </aside>
  );
};
