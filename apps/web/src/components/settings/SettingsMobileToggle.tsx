"use client";

import React from "react";
import { Menu } from "lucide-react";
import { useMissionStore } from "../../lib/store";
import { IconButton } from "../ui/IconButton";

/** Opens the settings sidebar on narrow viewports: icon-only, leftmost in the top bar. */
export const SettingsMobileToggle: React.FC = () => {
  const setIsSettingsNavOpen = useMissionStore((s) => s.setIsSettingsNavOpen);

  return (
    <IconButton label="Open Settings Menu" onClick={() => setIsSettingsNavOpen(true)} className="md:hidden">
      <Menu className="h-4 w-4 text-brand" />
    </IconButton>
  );
};
