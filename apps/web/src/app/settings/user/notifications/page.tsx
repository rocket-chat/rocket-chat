"use client";

import React from "react";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";

export default function UserPreferencesPage() {
  return (
    <div className="flex flex-col min-h-full">
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Preferences
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5">
            Personal behaviour of the agent and notifications. Organization-locked settings are shown but cannot be changed.
          </p>
        </div>
      </div>
      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto">
          <SchemaSettingsForm
            domain="user_preferences"
            mode="user"
            title="Agent & notifications"
            only={["suggest_next_questions", "slack_notifications_enabled"]}
          />
        </div>
      </div>
    </div>
  );
}
