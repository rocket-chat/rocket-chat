"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { Check, Copy, KeyRound } from "lucide-react";
import { Button } from "../ui/Button";
import { inboundCurl } from "../../lib/missions";

export interface ShownSecrets {
  missionName: string;
  /** The mission's inbound webhook: its URL and the secret its calls are signed with. */
  inbound?: { url: string; secret: string };
  /** The webhook delivery's signing secret. */
  webhook?: string;
}

const CopyBlock: React.FC<{ label: string; text: string; testId: string }> = ({ label, text, testId }) => {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-neutral-700 dark:text-neutral-300">{label}</span>
        <Button size="sm" variant="ghost" onClick={() => void copy()} aria-label={`Copy ${label.toLowerCase()}`}>
          {copied ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
        </Button>
      </div>
      <pre data-testid={testId} className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-surface-border bg-surface-subnav p-2 font-mono text-[11px] text-foreground">
        {text}
      </pre>
    </div>
  );
};

/**
 * A mission's secrets, shown once when they are made: the platform keeps them sealed and never
 * shows them again (rotating makes new ones).
 */
export const SecretDialog: React.FC<{ secrets: ShownSecrets; onClose: () => void }> = ({ secrets, onClose }) => {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} data-testid="mission-secret-dialog" className="w-full max-w-xl space-y-4 rounded-2xl border border-surface-border bg-surface-base p-5 shadow-2xl">
        <h2 id={titleId} className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <KeyRound className="h-4 w-4 text-brand" aria-hidden="true" /> Secrets of &quot;{secrets.missionName}&quot;
        </h2>
        <p className="text-xs text-neutral-700 dark:text-neutral-300">Copy them now: they are shown this once. Rotating one makes a new one.</p>
        {secrets.inbound && (
          <>
            <CopyBlock label="Webhook URL" text={secrets.inbound.url} testId="mission-inbound-url" />
            <CopyBlock label="Webhook secret" text={secrets.inbound.secret} testId="mission-inbound-secret" />
            <CopyBlock label="Calling it" text={inboundCurl(secrets.inbound.url, secrets.inbound.secret)} testId="mission-inbound-curl" />
          </>
        )}
        {secrets.webhook && <CopyBlock label="Delivery signing secret" text={secrets.webhook} testId="mission-webhook-secret" />}
        <div className="flex justify-end">
          <Button ref={closeRef} variant="primary" size="sm" data-testid="mission-secret-close" onClick={onClose}>
            I copied them
          </Button>
        </div>
      </div>
    </div>
  );
};
