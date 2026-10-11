import React from "react";
import { cn } from "./cn";

export const Card: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({ className, ...rest }) => (
  <div className={cn("rounded-xl border border-surface-border bg-surface-card", className)} {...rest} />
);

export const SectionTitle: React.FC<{ icon?: React.ReactNode; title: string; description?: string; className?: string }> = ({
  icon,
  title,
  description,
  className,
}) => (
  <div className={cn("space-y-1", className)}>
    <h2 className="flex items-center gap-2 font-display text-base font-semibold text-foreground">
      {icon && <span className="text-brand">{icon}</span>}
      {title}
    </h2>
    {description && <p className="text-sm text-neutral-600 dark:text-neutral-400">{description}</p>}
  </div>
);
