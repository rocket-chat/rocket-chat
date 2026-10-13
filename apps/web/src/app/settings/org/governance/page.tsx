"use client";

import React from "react";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";

export default function Page() {
  return (
    <div className="flex flex-col min-h-full">
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span>Organization</span>
            <span>/</span>
            <span className="text-foreground font-medium">Governance</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Governance & Memory
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5">
            Approvals, data handling, redaction, retention and long-term memory.
          </p>
        </div>
      </div>
      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-10">
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
        </div>
      </div>
    </div>
  );
}
