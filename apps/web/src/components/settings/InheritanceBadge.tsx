"use client";

import React from "react";
import { Lock, RotateCcw } from "lucide-react";

export type InheritanceStatus = "org_default" | "overridden" | "locked";

interface InheritanceBadgeProps {
  status: InheritanceStatus;
  onReset?: () => void;
  className?: string;
}

export const InheritanceBadge: React.FC<InheritanceBadgeProps> = ({
  status,
  onReset,
  className = "",
}) => {
  if (status === "locked") {
    return (
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-medium bg-secondary text-muted-foreground border border-border select-none ${className}`}
        title="Enforced by Organization Administrator (cannot be overridden)"
      >
        <Lock className="w-2.5 h-2.5" />
        <span>Locked by Admin</span>
      </span>
    );
  }

  if (status === "overridden") {
    return (
      <span
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-mono font-medium bg-primary/10 text-primary border border-primary/30 select-none ${className}`}
        title="Custom user override active"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-primary" />
        <span>Overridden</span>
        {onReset && (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onReset();
            }}
            className="hover:text-primary-hover p-0.5 rounded hover:bg-primary/20 transition-colors"
            title="Reset to Organization Default"
          >
            <RotateCcw className="w-2.5 h-2.5" />
          </button>
        )}
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-medium bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 select-none ${className}`}
      title="Inherited from Organization Default configuration"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
      <span>Org Default</span>
    </span>
  );
};
