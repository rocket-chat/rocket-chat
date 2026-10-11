import React from "react";
import Link from "next/link";
import { Terminal } from "lucide-react";
import { ThemeToggle } from "./Theme/ThemeToggle";
import { UserHeaderCapsule } from "./UserHeaderCapsule";
import { RocketChatIcon } from "./RocketChatLogo";

interface AppTopBarProps {
  /** The current page, shown after "Cockpit /". */
  current: { icon: React.ReactNode; label: string };
  /** Page-specific controls placed before the theme toggle. */
  leading?: React.ReactNode;
}

/** The bar every page outside the cockpit shares, so navigation looks and behaves the same. */
export const AppTopBar: React.FC<AppTopBarProps> = ({ current, leading }) => (
  <header className="z-30 flex h-14 flex-shrink-0 items-center justify-between border-b border-surface-border bg-surface-sidebar px-4 sm:px-6">
    <div className="flex min-w-0 items-center gap-4">
      <Link href="/" className="group flex items-center gap-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md border border-brand/30 bg-brand/10 text-brand transition-colors group-hover:bg-brand group-hover:text-white">
          <RocketChatIcon className="h-5 w-5" />
        </div>
        <span className="font-display text-sm font-semibold tracking-tight text-foreground">Rocket</span>
      </Link>
      <div className="h-4 w-px bg-surface-border" />
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-sm">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-neutral-500 transition-colors hover:text-foreground dark:text-neutral-400"
        >
          <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Cockpit</span>
        </Link>
        <span aria-hidden="true" className="text-neutral-400 dark:text-neutral-600">
          /
        </span>
        <span aria-current="page" className="flex items-center gap-1.5 truncate font-medium text-brand">
          {current.icon}
          <span className="truncate">{current.label}</span>
        </span>
      </nav>
    </div>
    <div className="flex items-center gap-2.5">
      {leading}
      <ThemeToggle />
      <div className="h-4 w-px bg-surface-border" />
      <UserHeaderCapsule />
    </div>
  </header>
);
