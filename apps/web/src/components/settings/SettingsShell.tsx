"use client";

import React from "react";
import { SettingsSaveProvider } from "./SettingsSave";

/** The settings page body: one scroll area, one sticky Save / Discard bar for every form in it. */
export const SettingsShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <main className="flex-1 overflow-y-auto bg-surface-base focus:outline-none" tabIndex={0} aria-label="Settings content">
    <SettingsSaveProvider>{children}</SettingsSaveProvider>
  </main>
);
