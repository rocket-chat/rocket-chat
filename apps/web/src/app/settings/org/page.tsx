"use client";

import React from "react";
import { SchemaSettingsForm } from "../../../components/settings/SchemaSettingsForm";

export default function Page() {
  return (
    <div className="flex flex-col min-h-full">
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span>Organization</span>
            <span>/</span>
            <span className="text-foreground font-medium">General</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            General
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5">
            Organization-wide defaults, sign-in policy and session privacy.
          </p>
        </div>
      </div>
      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-10">
          <SchemaSettingsForm
            domain="general"
            mode="org"
            title="General"
            description="Defaults that apply to every member of the organization."
          />
          <SchemaSettingsForm
            domain="user_preferences"
            mode="org"
            title="Member defaults"
            description="Defaults for personal settings. Lock a setting to stop members from overriding it."
            exclude={["full_name", "git_author_name", "git_author_email"]}
          />
        </div>
      </div>
    </div>
  );
}
