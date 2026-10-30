"use client";

import React from "react";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { TriggersPanel } from "../../../../components/settings/TriggersPanel";
import { OutboundWebhooksPanel } from "../../../../components/settings/OutboundWebhooksPanel";

export default function Page() {
  return (
    <SettingsPage
      section="Organization"
      title="Automation"
      description="What starts work on its own (GitHub, GitLab, Jira, Linear, a schedule, the API), and where the outcomes are reported."
    >
      <TriggersPanel />
      <OutboundWebhooksPanel />
    </SettingsPage>
  );
}
