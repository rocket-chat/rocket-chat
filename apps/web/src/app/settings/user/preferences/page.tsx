"use client";

import React from "react";
import { SchemaSettingsForm, type CustomControl } from "../../../../components/settings/SchemaSettingsForm";
import { RememberedApprovalsPanel } from "../../../../components/settings/RememberedApprovalsPanel";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { ThemeToggle } from "../../../../components/Theme/ThemeToggle";
import { ModelPicker } from "../../../../components/ui/ModelPicker";

const CONTROLS = {
  preferred_model: ({ id, value, disabled, onChange }: CustomControl) => (
    <ModelPicker
      id={id}
      data-testid="preferred-model"
      value={value}
      disabled={disabled}
      onChange={onChange}
      inheritLabel="Organization default"
    />
  ),
};

export default function Page() {
  return (
    <SettingsPage
      section="Personal"
      title="Preferences"
      description="Your model, how much the agent waits for you, notifications and appearance."
    >
      <SchemaSettingsForm
        domain="user_preferences"
        mode="user"
        title="Working with the agent"
        description="Each value follows the organization's default until you choose your own (an administrator may lock some)."
        only={["preferred_model", "permission_mode", "suggest_next_questions", "git_branch_prefix"]}
        controls={CONTROLS}
      />
      <SchemaSettingsForm
        domain="user_preferences"
        idScope="notify"
        mode="user"
        title="Notifications"
        description="Tell you when a turn ends or waits for you while its tab is in the background."
        only={["turn_notifications", "notification_sound"]}
      />
      <RememberedApprovalsPanel />
      <section className="space-y-3">
        <h2 className="font-display text-base font-semibold text-foreground">Appearance</h2>
        <div className="flex items-center gap-3 rounded-xl border border-surface-border bg-surface-card p-4 text-xs text-foreground">
          <span>Theme</span>
          <ThemeToggle />
        </div>
      </section>
    </SettingsPage>
  );
}
