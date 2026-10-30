"use client";

import React from "react";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { UsageBreakdown } from "../../../../components/settings/UsageBreakdown";

export default function Page() {
  return (
    <SettingsPage section="Personal" title="Usage" description="What your sessions spent this month.">
      <UsageBreakdown scope="user" />
    </SettingsPage>
  );
}
