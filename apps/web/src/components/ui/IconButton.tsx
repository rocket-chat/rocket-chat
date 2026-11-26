import React from "react";
import { cn } from "./cn";

export type IconButtonSize = "sm" | "md";

/** sm matches a text row that's already compact (a filter bar next to 28px segments); md is the
 *  default, standalone toolbar size. A plain className size class (h-7 w-7) silently loses to
 *  this component's own h-8 w-8 — same specificity, and cn() is a plain join with no override
 *  logic — so size is a real prop instead of something calling code tries to override. */
const SIZE_CLASS: Record<IconButtonSize, string> = {
  sm: "h-7 w-7",
  md: "h-8 w-8",
};

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** The accessible name and tooltip. Icon-only controls always need one. */
  label: string;
  /** For toggles: reflects whether the thing it controls is on. */
  pressed?: boolean;
  size?: IconButtonSize;
}

export const iconButtonClass = (pressed?: boolean, extra?: string, size: IconButtonSize = "md"): string =>
  cn(
    `inline-flex ${SIZE_CLASS[size]} shrink-0 items-center justify-center rounded-lg border transition-colors cursor-pointer`,
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60",
    pressed
      ? "border-brand/40 bg-brand/10 text-brand"
      : "border-surface-border bg-surface-card text-neutral-600 hover:bg-surface-elevated hover:text-foreground dark:text-neutral-400",
    extra
  );

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, pressed, size = "md", className, type = "button", children, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      className={iconButtonClass(pressed, className, size)}
      {...rest}
    >
      {children}
    </button>
  );
});
