"use client";

import React, { useId, useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "./cn";

interface DisclosureProps {
  summary: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  testId?: string;
}

/** Progressive disclosure: a one-line summary that opens to the detail, for advanced options. */
export const Disclosure: React.FC<DisclosureProps> = ({ summary, children, defaultOpen = false, className, testId }) => {
  const [open, setOpen] = useState(defaultOpen);
  const regionId = useId();
  return (
    <div className={className}>
      <button
        type="button"
        data-testid={testId}
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex cursor-pointer items-center gap-1.5 rounded-md py-1 text-sm text-neutral-600 hover:text-foreground dark:text-neutral-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
      >
        <ChevronRight className={cn("h-4 w-4 transition-transform", open && "rotate-90")} />
        {summary}
      </button>
      <div id={regionId} hidden={!open} className="pt-3">
        {open && children}
      </div>
    </div>
  );
};
