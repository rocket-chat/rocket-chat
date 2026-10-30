"use client";

import React from "react";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { GithubInstallationsPanel } from "../../../../components/settings/GithubInstallationsPanel";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function Page() {
  return (
    <SettingsPage
      section="Organization"
      title="Git & GitHub"
      description="The GitHub App, who publishes the agent's work, commit signing and the repositories the App acts on."
    >
      <SchemaSettingsForm
        domain="github"
        mode="org"
        title="Publishing and signing"
        description="Applied to every agent commit and pull request in this organization."
      />
      <GithubInstallationsPanel />
    </SettingsPage>
  );
}
