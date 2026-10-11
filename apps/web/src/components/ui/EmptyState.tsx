import React from "react";
import { cn } from "./cn";

/** What a list or page shows when there is nothing yet: say what it is and offer the next step. */
export const EmptyState: React.FC<{
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}> = ({ icon, title, description, action, className }) => (
  <div
    className={cn(
      "flex flex-col items-center gap-2 rounded-xl border border-dashed border-surface-border px-6 py-10 text-center",
      className
    )}
  >
    {icon && <div className="text-neutral-400">{icon}</div>}
    <p className="text-sm font-medium text-foreground">{title}</p>
    {description && <p className="max-w-sm text-sm text-neutral-600 dark:text-neutral-400">{description}</p>}
    {action && <div className="pt-2">{action}</div>}
  </div>
);
