import React from "react";
import Link from "next/link";
import { SettingsNav } from "../../components/settings/SettingsNav";
import { ThemeToggle } from "../../components/Theme/ThemeToggle";
import { UserHeaderCapsule } from "../../components/UserHeaderCapsule";
import { RocketChatIcon } from "../../components/RocketChatLogo";
import { Terminal, Settings } from "lucide-react";

export const metadata = {
  title: "Settings & Governance // Rocket Chat",
  description: "Enterprise configuration, custom agents, MCP registries, and security vault.",
};

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="h-screen w-screen flex flex-col bg-surface-base text-foreground font-sans overflow-hidden">
      {/* Stitch Settings Console Header */}
      <header className="h-14 px-4 sm:px-6 bg-surface-sidebar border-b border-surface-border flex items-center justify-between z-30 flex-shrink-0">
        <div className="flex items-center gap-4">
          {/* Brand Logo */}
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="w-7 h-7 rounded-md bg-brand/10 border border-brand/30 flex items-center justify-center text-brand transition-colors group-hover:bg-brand group-hover:text-white">
              <RocketChatIcon className="w-5 h-5" />
            </div>
            <div className="flex items-center gap-1.5">
              <span className="font-display font-semibold text-sm tracking-tight text-foreground">
                Rocket
              </span>
            </div>
          </Link>

          <div className="h-4 w-px bg-surface-border" />

          {/* Breadcrumb Navigation */}
          <nav className="flex items-center gap-2 text-xs">
            <Link
              href="/"
              className="text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-200 transition-colors flex items-center gap-1.5"
            >
              <Terminal className="w-3.5 h-3.5 text-neutral-400" />
              <span>Cockpit</span>
            </Link>
            <span className="text-neutral-400 dark:text-neutral-600">/</span>
            <span className="font-medium flex items-center gap-1.5 text-brand">
              <Settings className="w-3.5 h-3.5 text-brand" />
              <span>Settings Console</span>
            </span>
          </nav>
        </div>

        {/* Right Actions */}
        <div className="flex items-center gap-2.5">
          <ThemeToggle />

          <div className="h-4 w-px bg-surface-border" />

          {/* User profile capsule */}
          <UserHeaderCapsule />
        </div>
      </header>

      {/* Main Settings Console Body */}
      <div className="flex-1 flex overflow-hidden">
        <SettingsNav />
        <main className="flex-1 overflow-y-auto bg-surface-base">
          {children}
        </main>
      </div>
    </div>
  );
}
