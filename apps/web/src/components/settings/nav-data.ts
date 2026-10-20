import type React from "react";
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
  Users,
  SlidersHorizontal,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  requiresAdmin?: boolean;
}

export interface NavSection {
  title: string;
  hint: string;
  items: NavItem[];
}

// Grouped by who a page is for, not by what it is built from: what is yours, what the agent can
// use, and what only administrators change for everyone.
export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Personal",
    hint: "Only affects you",
    items: [
      { href: "/settings/user/profile", label: "Profile & preferences", icon: User },
      { href: "/settings/github", label: "Git & GitHub", icon: GitPullRequest },
      { href: "/settings/user/models", label: "My models & keys", icon: Cpu },
    ],
  },
  {
    title: "Agent capabilities",
    hint: "What the agent can use",
    items: [
      { href: "/settings/agents", label: "Agents", icon: Bot },
      { href: "/settings/subagents", label: "Subagents", icon: Cpu },
      { href: "/settings/mcp", label: "MCP servers", icon: Server },
      { href: "/settings/skills", label: "Skills", icon: Code2 },
      { href: "/settings/knowledge", label: "Knowledge bases", icon: Database },
    ],
  },
  {
    title: "Organization",
    hint: "Applies to everyone",
    items: [
      { href: "/settings/org/models", label: "Models & budgets", icon: Coins, requiresAdmin: true },
      { href: "/settings/org/governance", label: "Security & data", icon: ShieldCheck, requiresAdmin: true },
      { href: "/settings/org/sandboxes", label: "Sandboxes", icon: Box, requiresAdmin: true },
      { href: "/settings/org/users", label: "Users & roles", icon: Users, requiresAdmin: true },
      { href: "/settings/org/usage", label: "Usage & spend", icon: Coins },
      { href: "/settings/slack", label: "Slack", icon: MessageSquare },
      { href: "/settings/org", label: "Defaults", icon: SlidersHorizontal, requiresAdmin: true },
    ],
  },
];
