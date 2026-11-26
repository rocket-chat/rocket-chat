"use client";

import React from "react";
import { GitPullRequest } from "lucide-react";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { GithubInstallationsPanel } from "../../../../components/settings/GithubInstallationsPanel";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function Page() {
  return (
    <SettingsPage
      section="Resources"
      title="Git & GitHub"
      icon={<GitPullRequest className="h-5 w-5" aria-hidden="true" />}
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
