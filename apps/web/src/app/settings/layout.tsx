import React from "react";
import { AppTopBar } from "../../components/AppTopBar";
import { SettingsNav } from "../../components/settings/SettingsNav";
import { SettingsMobileToggle } from "../../components/settings/SettingsMobileToggle";
import { Settings } from "lucide-react";

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
    <div className="h-full w-full flex flex-col bg-surface-base text-foreground font-sans overflow-hidden">
      <AppTopBar
        current={{ icon: <Settings className="h-3.5 w-3.5" aria-hidden="true" />, label: "Settings Console" }}
        leading={<SettingsMobileToggle />}
      />

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
