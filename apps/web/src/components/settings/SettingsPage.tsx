"use client";

import React from "react";
import { cn } from "../ui/cn";
import { PageHeader } from "../ui/PageHeader";
import { PAGE_MAX_WIDTH } from "../ui/pageWidth";

interface SettingsPageProps {
  /** "Build", "Resources", "Governance" or "You": where the page sits in the navigation
   *  (matches a NAV_SECTIONS title, so the breadcrumb's middle segment lines up with the sidebar
   *  group the page is filed under). */
  section: string;
  title: string;
  description?: string;
  /** Same icon as the page's entry in the sidebar (NAV_SECTIONS), shown next to the title. */
  icon?: React.ReactNode;
  /** Buttons or controls on the header's right, e.g. "New agent". */
  actions?: React.ReactNode;
  /** Use the whole width: for editors with side panels (agents), not for forms. */
  wide?: boolean;
  children: React.ReactNode;
}

/** The breadcrumb, header and body every settings page shares — the one place their layout,
 *  column width and "Settings / {section} / {title}" trail are defined, so no page drifts into
 *  its own hand-rolled variant. */
export const SettingsPage: React.FC<SettingsPageProps> = ({ section, title, description, icon, actions, wide = false, children }) => (
  <div className="flex min-h-full flex-col">
    <div className="border-b border-surface-border bg-surface-subnav/50 px-4 pt-6 sm:px-8">
      <div className={cn("mx-auto", wide ? PAGE_MAX_WIDTH.wide : PAGE_MAX_WIDTH.normal)}>
        <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">
          <span>Settings</span>
          <span aria-hidden="true">/</span>
          <span>{section}</span>
          <span aria-hidden="true">/</span>
          <span className="font-medium text-foreground">{title}</span>
        </nav>
        <PageHeader title={title} description={description} icon={icon} actions={actions} className="px-0 py-0 pb-5" />
      </div>
    </div>
    <div className="flex-1 px-4 py-8 sm:px-8">
      <div className={cn("mx-auto space-y-10", wide ? PAGE_MAX_WIDTH.wide : PAGE_MAX_WIDTH.normal)}>{children}</div>
    </div>
  </div>
);
