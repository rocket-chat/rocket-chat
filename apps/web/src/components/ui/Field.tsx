import React, { useId } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./cn";

const CONTROL =
  "w-full rounded-lg border border-surface-border bg-surface-card text-sm text-foreground placeholder:text-neutral-500 " +
  "transition-colors hover:border-neutral-400 dark:hover:border-neutral-600 " +
  "focus:outline-none focus:border-brand/60 focus:ring-2 focus:ring-brand/20 disabled:opacity-60";

interface FieldProps {
  label: string;
  hint?: string;
  error?: string | null;
  className?: string;
  /** Receives the id the label points at; spread it on the control. */
  children: (control: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => React.ReactNode;
}

/** A labelled form row: the label is always tied to its control, hint and error are announced. */
export const Field: React.FC<FieldProps> = ({ label, hint, error, className, children }) => {
  const id = useId();
  const describedBy = error || hint ? `${id}-note` : undefined;
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id} className="block text-xs font-medium text-neutral-600 dark:text-neutral-300">
        {label}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {(error || hint) && (
        <p
          id={`${id}-note`}
          className={cn("text-xs", error ? "text-rose-600 dark:text-rose-400" : "text-neutral-500 dark:text-neutral-400")}
        >
          {error || hint}
        </p>
      )}
    </div>
  );
};

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} type="text" className={cn(CONTROL, "h-9 px-3", className)} {...rest} />;
  }
);

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cn(CONTROL, "px-3 py-2 leading-relaxed", className)} {...rest} />;
});

/** A native select (keyboard and screen-reader behaviour for free) drawn in the product's style. */
export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <div className="relative">
        <select ref={ref} className={cn(CONTROL, "h-9 appearance-none pl-3 pr-9", className)} {...rest}>
          {children}
        </select>
        <ChevronDown
          aria-hidden="true"
          className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-500"
        />
      </div>
    );
  }
);
