import React from "react";
import { cn } from "./cn";

export type ButtonVariant = "primary" | "secondary" | "flat" | "dashed" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-brand text-white border border-brand hover:bg-brand-hover hover:border-brand-hover",
  secondary:
    "bg-surface-card text-foreground border border-surface-border hover:border-brand/50 hover:bg-surface-elevated",
  // For a trigger that opens something in place (a dropdown, a search overlay) rather than acting
  // directly: a quieter, translucent twin of "secondary" so the page doesn't read as a wall of
  // identical solid buttons.
  flat: "bg-surface-card/60 text-foreground border border-surface-border hover:border-brand/40 hover:bg-surface-elevated",
  // An empty "add new X" slot: dashed so it reads as a placeholder, not a committed action.
  dashed:
    "bg-transparent text-foreground border border-dashed border-surface-border hover:border-brand/50 hover:bg-surface-elevated",
  ghost:
    "bg-transparent text-neutral-500 dark:text-neutral-400 border border-transparent hover:text-foreground hover:bg-surface-elevated",
  danger:
    "bg-transparent text-rose-600 dark:text-rose-400 border border-rose-500/40 hover:bg-rose-500/10",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
};

/**
 * The exact classes a `Button` renders with, for the handful of spots that can't be a `<button>`
 * (an `<a>`/`Link` styled as a button, e.g. "Back to the cockpit") but still need to look and
 * behave like one — kept in sync here so those never drift into their own one-off variant again.
 */
export function buttonClass(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  opts?: { fullWidth?: boolean; className?: string }
): string {
  return cn(
    "inline-flex items-center justify-center rounded-lg font-medium transition-colors cursor-pointer",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 focus-visible:ring-offset-1 focus-visible:ring-offset-surface-base",
    "disabled:opacity-50 disabled:cursor-not-allowed",
    VARIANTS[variant],
    SIZES[size],
    opts?.fullWidth && "w-full",
    opts?.className
  );
}

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Stretches to fill its container, e.g. a sidebar action. */
  fullWidth?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", fullWidth, className, type = "button", ...rest },
  ref
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, { fullWidth, className })} {...rest} />;
});
