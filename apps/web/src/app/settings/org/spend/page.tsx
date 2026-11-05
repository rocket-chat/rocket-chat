"use client";

import React from "react";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { SpendLimitsPanel } from "../../../../components/settings/SpendLimitsPanel";
import { UsageBreakdown } from "../../../../components/settings/UsageBreakdown";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { NotManaged } from "../../../../components/settings/NotManaged";
import { useManagedScopes } from "../../../../lib/useOwnedSections";

export default function Page() {
  const { access, showOrg, teams, unmanaged } = useManagedScopes();
  return (
    <SettingsPage
      section="Governance"
      title="Spend & usage"
      description="Limits on what the organization, its teams, members and agents spend, and where the money goes."
    >
      {unmanaged && <NotManaged team={unmanaged} what="spend limits" />}
      {showOrg && <SpendLimitsPanel />}
      {teams.map((team) => (
        <SpendLimitsPanel key={team.id} team={team} />
      ))}
      {showOrg && <UsageBreakdown scope="org" />}
      {showOrg && (
        <SchemaSettingsForm
          domain="budgets"
          mode="org"
          title="Turn and task limits"
          description="How far one turn, and an automatically continued task, may go."
        />
      )}
      {access.loaded && !showOrg && teams.length === 0 && !unmanaged && <NotManaged team={null} what="spend limits" />}
    </SettingsPage>
  );
}
