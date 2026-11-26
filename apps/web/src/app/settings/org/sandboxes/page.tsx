"use client";

import React from "react";
import { Box } from "lucide-react";
import { RepositorySetupsPanel } from "../../../../components/settings/RepositorySetupsPanel";
import { SandboxCataloguePanel } from "../../../../components/settings/SandboxCataloguePanel";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";
import { NotManaged } from "../../../../components/settings/NotManaged";
import { useManagedScopes } from "../../../../lib/useOwnedSections";
import { SettingsPage } from "../../../../components/settings/SettingsPage";

export default function Page() {
  const { access, showOrg, teams, unmanaged } = useManagedScopes();
  return (
    <SettingsPage
      section="Resources"
      title="Sandboxes"
      icon={<Box className="h-5 w-5" aria-hidden="true" />}
      description="The sandboxes members can choose, what each can do, and how much compute they get."
    >
      {unmanaged && <NotManaged team={unmanaged} what="sandboxes" />}
      {showOrg && <SandboxCataloguePanel />}
      {teams.map((team) => (
        <SandboxCataloguePanel key={team.id} team={team} />
      ))}
      {showOrg && <RepositorySetupsPanel />}
      {showOrg && (
        <SchemaSettingsForm
          domain="sandboxes"
          mode="org"
          title="Sandbox runtime"
          description="Compute limits and behaviour shared by every sandbox."
          exclude={["default_image", "allowed_images"]}
        />
      )}
      {access.loaded && !showOrg && teams.length === 0 && !unmanaged && <NotManaged team={null} what="sandboxes" />}
    </SettingsPage>
  );
}
