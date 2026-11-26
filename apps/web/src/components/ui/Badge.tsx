import React from "react";
import { cn } from "./cn";

export type BadgeTone = "neutral" | "brand" | "success" | "warning" | "danger" | "info" | "purple";
/**
 * Shape carries meaning, held to consistently everywhere a badge appears: "tag" (square) names
 * a category, entity or source (Org, a skill's source repo, a connector kind); "pill" (round)
 * reports a status, state or count (On/Off, a provenance, a live mode). Border opacity is fixed
 * at 25% for every tone so no single badge reads brighter than the rest.
 */
export type BadgeShape = "tag" | "pill";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-elevated text-neutral-600 dark:text-neutral-300 border-surface-border",
  brand: "bg-brand/10 text-brand border-brand/25",
  success: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25",
  warning: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25",
  danger: "bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/25",
  info: "bg-cyan/10 text-cyan border-cyan/25",
  purple: "bg-violet-500/10 text-violet-400 border-violet-500/25",
};

const SHAPES: Record<BadgeShape, string> = {
  tag: "rounded-md",
  pill: "rounded-full",
};

/** A small status or category label; kept compact and quiet. */
export const Badge: React.FC<
  React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; shape?: BadgeShape }
> = ({ tone = "neutral", shape = "tag", className, ...rest }) => (
  <span
    className={cn(
      // [&>svg] pins every icon a caller passes in to one compact size, !important so it wins
      // regardless of whatever h-* w-* the caller put on its own <Icon> (several badges around
      // the app pass icons sized for a full button, which towered over this flatter pill).
      "inline-flex items-center gap-1 border px-1.5 py-px text-[11px] leading-none font-medium tracking-wide [&>svg]:!h-2.5 [&>svg]:!w-2.5",
      TONES[tone],
      SHAPES[shape],
      className
    )}
    {...rest}
  />
);
