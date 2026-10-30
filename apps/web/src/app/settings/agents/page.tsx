"use client";

import React from "react";
import { AgentsManager } from "../../../components/agents/AgentsManager";
import { SettingsPage } from "../../../components/settings/SettingsPage";

export default function AgentsSettingsPage() {
  return (
    <SettingsPage
      section="Organization"
      title="Agents"
      description="The organization's agents and the subagent roles they delegate to, in one editor."
    >
      <AgentsManager scope="org" />
    </SettingsPage>
  );
}
