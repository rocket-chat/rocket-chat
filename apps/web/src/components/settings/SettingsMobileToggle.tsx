"use client";

import React from "react";
import { Menu } from "lucide-react";
import { useMissionStore } from "../../lib/store";

export const SettingsMobileToggle: React.FC = () => {
  const setIsSettingsNavOpen = useMissionStore((s) => s.setIsSettingsNavOpen);

  return (
    <button
      type="button"
      onClick={() => setIsSettingsNavOpen(true)}
      className="md:hidden p-1.5 rounded-lg border border-surface-border bg-surface-card hover:bg-surface-elevated text-neutral-400 hover:text-foreground cursor-pointer flex items-center gap-1.5 text-xs"
      title="Open Settings Menu"
      aria-label="Open Settings Menu"
    >
      <Menu className="w-4 h-4 text-brand" />
      <span className="hidden sm:inline">Menu</span>
    </button>
  );
};
