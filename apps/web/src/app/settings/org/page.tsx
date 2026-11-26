"use client";

import React from "react";
import { Building2 } from "lucide-react";
import { SchemaSettingsForm } from "../../../components/settings/SchemaSettingsForm";
import { OrganizationNameCard } from "../../../components/settings/OrganizationNameCard";
import { SetupHealthCard } from "../../../components/settings/SetupHealthCard";
import { defaultAgentControl } from "../../../components/settings/DefaultAgentPicker";
import { SettingsPage } from "../../../components/settings/SettingsPage";

const MEMBER_DEFAULT_CONTROLS = { default_agent: defaultAgentControl("None: the platform's default") };

export default function Page() {
  return (
    <SettingsPage
      section="Governance"
      title="Organization"
      icon={<Building2 className="h-5 w-5" aria-hidden="true" />}
      description="Organization-wide defaults, sign-in policy and session privacy."
    >
      <OrganizationNameCard />
      <SetupHealthCard />
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
        controls={MEMBER_DEFAULT_CONTROLS}
      />
    </SettingsPage>
  );
}
