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

/** Which owners a page is about: the owner filter shows only the pages of the owner it names. */
export type NavOwner = "me" | "team" | "org";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Shown to administrators only (hidden, not dimmed, for everyone else). */
  requiresAdmin?: boolean;
  /** With `requiresAdmin`: also shown to a member of at least one team. */
  orTeamMember?: boolean;
  /** With `requiresAdmin`: also shown to someone who manages at least one team. */
  orTeamManager?: boolean;
  /** Shown only when the member's role holds this capability. */
  capability?: Capability;
  /** The owners whose objects or settings the page holds (absent: it follows no owner). */
  owners?: NavOwner[];
}

export interface NavSection {
  title: string;
  hint: string;
  items: NavItem[];
}

const EVERY_OWNER: NavOwner[] = ["me", "team", "org"];

// Organized by topic; each object is owned by a member, a team or the organization, and the owner
// filter at the top of the navigation narrows the pages and lists to one owner. Each page shows a
// few core fields and folds the rest under Advanced.
export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Build",
    hint: "What agents are made of",
    items: [
      { href: "/settings/agents", label: "Agents", icon: Bot, owners: EVERY_OWNER },
      { href: "/settings/skills", label: "Skills", icon: Code2, owners: EVERY_OWNER },
      { href: "/settings/mcp", label: "MCP servers", icon: Server, owners: EVERY_OWNER },
      { href: "/settings/knowledge", label: "Knowledge bases", icon: Database, owners: EVERY_OWNER },
    ],
  },
  {
    title: "Resources",
    hint: "What they run on",
    items: [
      { href: "/settings/org/models", label: "Models & keys", icon: Cpu, requiresAdmin: true, orTeamManager: true, owners: ["team", "org"] },
      { href: "/settings/user/keys", label: "My keys", icon: KeyRound, capability: "bring_own_keys", owners: ["me"] },
      { href: "/settings/org/sandboxes", label: "Sandboxes", icon: Box, requiresAdmin: true, owners: ["org"] },
      { href: "/settings/org/git", label: "Git & GitHub", icon: GitPullRequest, requiresAdmin: true, owners: ["org"] },
      // Every member links their own Slack account here; the page hides bots and the workspace from non-administrators.
      { href: "/settings/slack", label: "Slack", icon: MessageSquare, owners: ["me", "org"] },
    ],
  },
  {
    title: "Governance",
    hint: "Who may do and spend what",
    items: [
      { href: "/settings/org", label: "Organization", icon: Building2, requiresAdmin: true, owners: ["org"] },
      { href: "/settings/members", label: "Members & teams", icon: Users, requiresAdmin: true, orTeamMember: true, owners: ["team", "org"] },
      { href: "/settings/org/spend", label: "Spend & usage", icon: Coins, requiresAdmin: true, orTeamManager: true, owners: ["team", "org"] },
      { href: "/settings/user/usage", label: "My usage", icon: Coins, owners: ["me"] },
      { href: "/settings/org/automation", label: "Automation", icon: Workflow, requiresAdmin: true, owners: ["org"] },
      { href: "/settings/org/governance", label: "Security & data", icon: ShieldCheck, requiresAdmin: true, owners: ["org"] },
      { href: "/settings/org/audit", label: "Audit log", icon: ScrollText, requiresAdmin: true, owners: ["org"] },
    ],
  },
  {
    title: "You",
    hint: "Only affects you",
    items: [
      { href: "/settings/user/profile", label: "Profile", icon: User },
      { href: "/settings/user/preferences", label: "Preferences", icon: SlidersHorizontal },
      { href: "/settings/github", label: "Git identity", icon: GitPullRequest },
      { href: "/settings/user/api-tokens", label: "API tokens", icon: Webhook },
    ],
  },
];
