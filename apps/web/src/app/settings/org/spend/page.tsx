"use client";

import React from "react";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { SpendLimitsPanel } from "../../../../components/settings/SpendLimitsPanel";
import { UsageBreakdown } from "../../../../components/settings/UsageBreakdown";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";

export default function Page() {
  return (
    <SettingsPage
      section="Organization"
      title="Spend"
      description="Limits on what the organization, its members and its agents spend, and where the money goes."
    >
      <SpendLimitsPanel />
      <UsageBreakdown scope="org" />
      <SchemaSettingsForm
        domain="budgets"
        mode="org"
        title="Turn and task limits"
        description="How far one turn, and an automatically continued task, may go."
      />
    </SettingsPage>
  );
}
