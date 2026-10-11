import React from "react";
import { cn } from "./cn";

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** The accessible name and tooltip. Icon-only controls always need one. */
  label: string;
  /** For toggles: reflects whether the thing it controls is on. */
  pressed?: boolean;
}

export const iconButtonClass = (pressed?: boolean, extra?: string): string =>
  cn(
    "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors cursor-pointer",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60",
    pressed
      ? "border-brand/40 bg-brand/10 text-brand"
      : "border-surface-border bg-surface-card text-neutral-600 hover:bg-surface-elevated hover:text-foreground dark:text-neutral-400",
    extra
  );

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, pressed, className, type = "button", children, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      className={iconButtonClass(pressed, className)}
      {...rest}
    >
      {children}
    </button>
  );
});
