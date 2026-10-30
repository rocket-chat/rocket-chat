"use client";

import React from "react";
import { CredentialsPanel } from "../../../../components/settings/CredentialsPanel";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function Page() {
  return (
    <SettingsPage
      section="Personal"
      title="My keys"
      description="Provider keys you pay for. Your organization decides when they are used (Models > Members' own keys)."
    >
      <CredentialsPanel mode="user" />
    </SettingsPage>
  );
}
