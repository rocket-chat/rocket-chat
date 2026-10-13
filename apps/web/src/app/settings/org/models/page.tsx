"use client";

import React from "react";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { ProviderKeysPanel } from "../../../../components/settings/ProviderKeysPanel";

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
            <span className="text-foreground font-medium">Models</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Models & Routing
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5">
            Which models members may use, fail-over, sampling defaults and cost routing.
          </p>
        </div>
      </div>
      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-10">
          <SchemaSettingsForm
            domain="models"
            mode="org"
            title="Model policy"
            description="Allow and deny lists apply to every model choice, including fail-over."
          />
          <SchemaSettingsForm
            domain="routing"
            mode="org"
            title="Cost routing"
            description="Send auxiliary work (titles, suggestions, summaries) to a cheaper model."
          />
          <SchemaSettingsForm
            domain="budgets"
            mode="org"
            title="Budgets & provider keys"
            description="Spending caps stop new model calls before they are made (0 = no cap); the key policy decides whose provider keys may be used."
          />
          <ProviderKeysPanel mode="org" />
        </div>
      </div>
    </div>
  );
}
