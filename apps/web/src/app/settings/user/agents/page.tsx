"use client";

import React from "react";
import { AgentsManager } from "../../../../components/agents/AgentsManager";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function MyAgentsPage() {
  return (
    <SettingsPage section="Personal" title="My agents" description="Agents of your own; only you can use them.">
      <AgentsManager scope="mine" />
    </SettingsPage>
  );
}
