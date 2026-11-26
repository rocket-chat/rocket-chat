"use client";

import React from "react";
import { Coins } from "lucide-react";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { UsageBreakdown } from "../../../../components/settings/UsageBreakdown";

export default function Page() {
  return (
    <SettingsPage
      section="Governance"
      title="Usage"
      icon={<Coins className="h-5 w-5" aria-hidden="true" />}
      description="What your sessions spent this month."
    >
      <UsageBreakdown scope="user" />
    </SettingsPage>
  );
}
