"use client";

import React from "react";
import Link from "next/link";
import { Workflow } from "lucide-react";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { OutboundWebhooksPanel } from "../../../../components/settings/OutboundWebhooksPanel";

export default function Page() {
  return (
    <SettingsPage
      section="Governance"
      title="Automation"
      description="Where the organization's outcomes are reported, whatever started them."
    >
      <p
        data-testid="automation-missions-note"
        className="flex items-start gap-2 rounded-lg border border-border/70 bg-surface-card/60 p-3 text-sm text-neutral-700 dark:text-neutral-300"
      >
        <Workflow className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
        <span>
          What starts work on its own (a schedule, a GitHub or GitLab event, Jira, Linear, a webhook of its own) is now a
          mission, with where its result goes.{" "}
          <Link href="/missions" className="font-medium text-brand underline-offset-2 hover:underline">
            Open Missions
          </Link>
        </span>
      </p>
      <OutboundWebhooksPanel />
    </SettingsPage>
  );
}
