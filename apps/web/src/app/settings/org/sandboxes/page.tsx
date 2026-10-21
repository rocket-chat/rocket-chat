"use client";

import React from "react";
import { RepositorySetupsPanel } from "../../../../components/settings/RepositorySetupsPanel";
import { SandboxCataloguePanel } from "../../../../components/settings/SandboxCataloguePanel";
import { SchemaSettingsForm } from "../../../../components/settings/SchemaSettingsForm";

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
            <span className="text-foreground font-medium">Sandboxes</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Sandbox Fleet & Images
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5">
            The sandboxes members can choose, what each can do, and how much compute they get.
          </p>
        </div>
      </div>
      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-10">
          <SandboxCataloguePanel />
          <RepositorySetupsPanel />
          <SchemaSettingsForm
            domain="sandboxes"
            mode="org"
            title="Sandbox runtime"
            description="Compute limits and behaviour shared by every sandbox."
            exclude={["default_image", "allowed_images"]}
          />
        </div>
      </div>
    </div>
  );
}
