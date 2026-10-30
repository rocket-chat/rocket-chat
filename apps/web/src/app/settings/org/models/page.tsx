"use client";

import React from "react";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { CredentialsPanel } from "../../../../components/settings/CredentialsPanel";
import type { CustomControl } from "../../../../components/settings/SchemaSettingsForm";
import { SettingsPage } from "../../../../components/settings/SettingsPage";
import { ModelPicker } from "../../../../components/ui/ModelPicker";

function modelControl(inheritLabel: string) {
  return function ModelControl({ id, value, disabled, onChange }: CustomControl) {
    return (
      <ModelPicker
        id={id}
        data-testid={id}
        value={value}
        disabled={disabled}
        onChange={onChange}
        inheritLabel={inheritLabel}
      />
    );
  };
}

const MODEL_CONTROLS = {
  default_model: modelControl("Platform default"),
  fast_model: modelControl("Not set"),
  economy_model: modelControl("Not set"),
};

export default function Page() {
  return (
    <SettingsPage
      section="Organization"
      title="Models & credentials"
      description="The keys the organization pays for, which models members may use, fail-over and routing."
    >
      <CredentialsPanel mode="org" />
      <SchemaSettingsForm
        domain="models"
        mode="org"
        title="Model policy"
        description="Allow and deny lists apply to every model choice, including fail-over and routing tiers."
        controls={MODEL_CONTROLS}
      />
    </SettingsPage>
  );
}
