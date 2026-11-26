"use client";

import React from "react";
import { KeyRound } from "lucide-react";
import { CredentialsPanel } from "../../../../components/settings/CredentialsPanel";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function Page() {
  return (
    <SettingsPage
      section="Resources"
      title="My keys"
      icon={<KeyRound className="h-5 w-5" aria-hidden="true" />}
      description="Provider keys you pay for. Your organization decides when they are used (Models > Members' own keys)."
    >
      <CredentialsPanel mode="user" />
    </SettingsPage>
  );
}
