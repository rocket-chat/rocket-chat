"use client";

import React from "react";
import { AgentsManager } from "../../../components/agents/AgentsManager";
import { SettingsPage } from "../../../components/settings/SettingsPage";

export default function AgentsSettingsPage() {
  return (
    <SettingsPage
      section="Build"
      title="Agents"
      description="Your agents, your teams' and the organization's, and the subagent roles they delegate to, in one editor."
    >
      <AgentsManager />
    </SettingsPage>
  );
}
