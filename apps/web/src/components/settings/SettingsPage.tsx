"use client";

import React from "react";

interface SettingsPageProps {
  /** "Personal", "Organization" or "Integrations": where the page sits in the navigation. */
  section: string;
  title: string;
  description?: string;
  children: React.ReactNode;
}

/** The header and body every settings page shares. */
export const SettingsPage: React.FC<SettingsPageProps> = ({ section, title, description, children }) => (
  <div className="flex min-h-full flex-col">
    <div className="border-b border-surface-border bg-surface-subnav/50 px-4 py-6 sm:px-8">
      <div className="mx-auto max-w-5xl">
        <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">
          <span>Settings</span>
          <span aria-hidden="true">/</span>
          <span>{section}</span>
          <span aria-hidden="true">/</span>
          <span className="font-medium text-foreground">{title}</span>
        </nav>
        <h1 className="font-display text-xl font-bold tracking-tight text-foreground md:text-2xl">{title}</h1>
        {description && <p className="mt-1.5 text-xs text-neutral-500 dark:text-neutral-400">{description}</p>}
      </div>
    </div>
    <div className="flex-1 px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-5xl space-y-10">{children}</div>
    </div>
  </div>
);
