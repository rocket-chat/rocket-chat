"use client";

import React from "react";
import { Bot } from "lucide-react";
import { AgentsManager } from "../../../components/agents/AgentsManager";
import { SettingsPage } from "../../../components/settings/SettingsPage";

export default function AgentsSettingsPage() {
  return (
    <SettingsPage
      section="Build"
      wide
      title="Agents"
      icon={<Bot className="h-5 w-5" aria-hidden="true" />}
      description="Your agents, your teams' and the organization's, and the subagent roles they delegate to, in one editor."
    >
      <AgentsManager />
    </SettingsPage>
  );
}
