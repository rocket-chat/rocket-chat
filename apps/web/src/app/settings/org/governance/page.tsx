"use client";

import React from "react";
import { ShieldCheck } from "lucide-react";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function Page() {
  return (
    <SettingsPage
      section="Governance"
      title="Security & data"
      icon={<ShieldCheck className="h-5 w-5" aria-hidden="true" />}
      description="Approvals, data handling, redaction, retention and long-term memory."
    >
      <SchemaSettingsForm
        domain="governance"
        mode="org"
        title="Governance & data controls"
        description="Server-enforced policy for approvals, providers, egress, redaction and retention."
      />
      <SchemaSettingsForm
        domain="memory"
        mode="org"
        title="Long-term memory"
        description="What the agent may remember across sessions."
      />
    </SettingsPage>
  );
}
