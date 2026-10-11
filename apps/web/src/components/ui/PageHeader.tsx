import React from "react";
import { cn } from "./cn";

interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}

/** The heading block every page opens with: sans title, quiet description, actions on the right. */
export const PageHeader: React.FC<PageHeaderProps> = ({ title, description, icon, actions, className }) => (
  <header className={cn("flex flex-wrap items-start justify-between gap-4 pb-5", className)}>
    <div className="min-w-0 space-y-1">
      <h1 className="flex items-center gap-2.5 font-display text-xl font-semibold tracking-tight text-foreground">
        {icon && <span className="text-brand">{icon}</span>}
        <span className="truncate">{title}</span>
      </h1>
      {description && (
        <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">{description}</p>
      )}
    </div>
    {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
  </header>
);

/** The padded, centred column a page's content lives in. */
export const PageBody: React.FC<{ children: React.ReactNode; wide?: boolean; className?: string }> = ({
  children,
  wide,
  className,
}) => (
  <div className={cn("mx-auto w-full px-4 py-8 sm:px-8", wide ? "max-w-6xl" : "max-w-3xl", className)}>
    {children}
  </div>
);
