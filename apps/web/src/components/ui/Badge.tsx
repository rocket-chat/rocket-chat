import React from "react";
import { cn } from "./cn";

export type BadgeTone = "neutral" | "brand" | "success" | "warning" | "danger" | "info";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-elevated text-neutral-600 dark:text-neutral-300 border-surface-border",
  brand: "bg-brand/10 text-brand border-brand/25",
  success: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25",
  warning: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25",
  danger: "bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/25",
  info: "bg-cyan/10 text-cyan border-cyan/25",
};

/** A small status or category label; kept compact and quiet. */
export const Badge: React.FC<
  React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }
> = ({ tone = "neutral", className, ...rest }) => (
  <span
    className={cn(
      "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium tracking-wide",
      TONES[tone],
      className
    )}
    {...rest}
  />
);
