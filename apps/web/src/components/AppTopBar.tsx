import React from "react";
import Link from "next/link";
import { Terminal } from "lucide-react";
import { ThemeToggle } from "./Theme/ThemeToggle";
import { UserHeaderCapsule } from "./UserHeaderCapsule";
import { BrandLink } from "./RocketChatLogo";

interface AppTopBarProps {
  /** The current page, shown after "Cockpit /". */
  current: { icon: React.ReactNode; label: string };
  /** A page's mobile sidebar toggle: the leftmost thing in the bar, before the logo, icon-only. */
  mobileMenu?: React.ReactNode;
  /** Page-specific controls placed before the theme toggle. */
  actions?: React.ReactNode;
}

/** The bar every page outside the cockpit shares, so navigation looks and behaves the same. */
export const AppTopBar: React.FC<AppTopBarProps> = ({ current, mobileMenu, actions }) => (
  <header className="z-30 flex h-14 flex-shrink-0 items-center justify-between gap-2 border-b border-surface-border bg-surface-sidebar pl-3 pr-1.5 sm:pl-4 sm:pr-2">
    <div className="flex min-w-0 flex-1 items-center gap-3 sm:gap-4">
      {mobileMenu}
      <BrandLink label="Rocket" labelClassName="hidden sm:inline" className="shrink-0" />
      <div className="hidden h-4 w-px shrink-0 bg-surface-border sm:block" />
      <nav aria-label="Breadcrumb" className="flex min-w-0 flex-1 items-center gap-2 text-sm">
        <Link
          href="/"
          className="hidden shrink-0 items-center gap-1.5 whitespace-nowrap text-neutral-500 transition-colors hover:text-foreground dark:text-neutral-400 sm:flex"
        >
          <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Cockpit</span>
        </Link>
        <span aria-hidden="true" className="hidden text-neutral-400 dark:text-neutral-600 sm:inline">
          /
        </span>
        <span aria-current="page" className="flex min-w-0 items-center gap-1.5 truncate font-medium text-brand">
          {current.icon}
          <span className="truncate">{current.label}</span>
        </span>
      </nav>
    </div>
    <div className="flex shrink-0 items-center gap-2.5">
      {actions}
      <ThemeToggle />
      <div className="h-4 w-px bg-surface-border" />
      <UserHeaderCapsule />
    </div>
  </header>
);
