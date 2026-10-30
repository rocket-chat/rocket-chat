import type React from "react";
import {
  Bot,
  Box,
  Building2,
  Code2,
  Coins,
  Cpu,
  Database,
  GitPullRequest,
  KeyRound,
  MessageSquare,
  ScrollText,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  User,
  Users,
  Webhook,
  Workflow,
} from "lucide-react";
import type { Capability } from "../../lib/access";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Shown to administrators only (hidden, not dimmed, for everyone else). */
  requiresAdmin?: boolean;
  /** Shown only when the member's role holds this capability. */
  capability?: Capability;
}

export interface NavSection {
  title: string;
  hint: string;
  items: NavItem[];
}

// Organized by topic, with what is yours apart from what only administrators change for
// everyone (P4.1). Each page shows a few core fields and folds the rest under Advanced.
export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Personal",
    hint: "Only affects you",
    items: [
      { href: "/settings/user/profile", label: "Profile", icon: User },
      { href: "/settings/user/preferences", label: "Preferences", icon: SlidersHorizontal },
      { href: "/settings/user/keys", label: "My keys", icon: KeyRound, capability: "bring_own_keys" },
      { href: "/settings/user/agents", label: "My agents", icon: Bot, capability: "create_personal_agents" },
      { href: "/settings/github", label: "Git identity", icon: GitPullRequest },
      { href: "/settings/user/usage", label: "Usage", icon: Coins },
      { href: "/settings/user/api-tokens", label: "API tokens", icon: Webhook },
    ],
  },
  {
    title: "Integrations",
    hint: "What the agent can reach",
    items: [
      { href: "/settings/mcp", label: "MCP servers", icon: Server },
      { href: "/settings/skills", label: "Skills", icon: Code2 },
      { href: "/settings/knowledge", label: "Knowledge bases", icon: Database },
      // Every member links their own Slack account here; the page hides bots and the workspace from non-administrators.
      { href: "/settings/slack", label: "Slack", icon: MessageSquare },
    ],
  },
  {
    title: "Organization",
    hint: "Applies to everyone",
    items: [
      { href: "/settings/org", label: "General", icon: Building2, requiresAdmin: true },
      { href: "/settings/org/users", label: "Members & roles", icon: Users, requiresAdmin: true },
      { href: "/settings/org/models", label: "Models & credentials", icon: Cpu, requiresAdmin: true },
      { href: "/settings/org/spend", label: "Spend", icon: Coins, requiresAdmin: true },
      { href: "/settings/agents", label: "Agents", icon: Bot, requiresAdmin: true },
      { href: "/settings/org/git", label: "Git & GitHub", icon: GitPullRequest, requiresAdmin: true },
      { href: "/settings/org/automation", label: "Automation", icon: Workflow, requiresAdmin: true },
      { href: "/settings/org/sandboxes", label: "Sandboxes", icon: Box, requiresAdmin: true },
      { href: "/settings/org/governance", label: "Security & data", icon: ShieldCheck, requiresAdmin: true },
      { href: "/settings/org/audit", label: "Audit log", icon: ScrollText, requiresAdmin: true },
    ],
  },
];
