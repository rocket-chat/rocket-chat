"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, Globe, Link2Off, Users, X } from "lucide-react";
import { Button } from "./ui/Button";
import { Field, Select, Textarea } from "./ui/Field";

interface Member {
  user_id: string;
  email: string;
  name: string;
}

interface Sharing {
  org_wide: boolean;
  members: Member[];
  unknown_emails: string[];
  public_links_enabled: boolean;
  public_link: { url: string; expires_at: number } | null;
}

interface ShareDialogProps {
  sessionId: string;
  onClose: () => void;
}

async function failure(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
}

/** Who can open a session: members by email, the whole organization, and/or a read-only public link. */
export const ShareDialog: React.FC<ShareDialogProps> = ({ sessionId, onClose }) => {
  const [sharing, setSharing] = useState<Sharing | null>(null);
  const [emails, setEmails] = useState("");
  const [orgWide, setOrgWide] = useState(false);
  const [days, setDays] = useState("7");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  const adopt = useCallback((next: Sharing) => {
    setSharing(next);
    setOrgWide(next.org_wide);
    setEmails(next.members.map((m) => m.email).join("\n"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await fetch(`/v1/sessions/${sessionId}/sharing`);
      if (cancelled) return;
      if (!res.ok) return setError(await failure(res));
      adopt(await res.json());
    })().catch(() => !cancelled && setError("Could not load sharing settings."));
    return () => {
      cancelled = true;
    };
  }, [sessionId, adopt]);

  useEffect(() => {
    dialogRef.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const JSON_HEADERS = { "Content-Type": "application/json" };

  const settle = async (call: Promise<Response>): Promise<Sharing | null> => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await call;
      if (!res.ok) {
        setError(await failure(res));
        return null;
      }
      return (await res.json()) as Sharing;
    } catch {
      setError("The request failed. Check your connection and try again.");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const saveMembers = async () => {
    const next = await settle(
      fetch(`/v1/sessions/${sessionId}/sharing`, {
        method: "PUT",
        headers: JSON_HEADERS,
        body: JSON.stringify({ org_wide: orgWide, emails: [emails] }),
      })
    );
    if (!next) return;
    adopt(next);
    setNotice(
      next.unknown_emails.length
        ? `Saved. Not members of this organization, so not added: ${next.unknown_emails.join(", ")}`
        : "Saved."
    );
  };

  const createLink = async () => {
    const next = await settle(
      fetch(`/v1/sessions/${sessionId}/sharing/link`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ expires_in_days: Number(days) }),
      })
    );
    if (next) setSharing(next);
  };

  const revokeLink = async () => {
    const next = await settle(fetch(`/v1/sessions/${sessionId}/sharing/link`, { method: "DELETE" }));
    if (next) setSharing(next);
  };

  const linkUrl = sharing?.public_link ? `${window.location.origin}${sharing.public_link.url}` : "";

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(linkUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Copy failed; select the link and copy it manually.");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-title"
        data-testid="share-dialog"
        className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-surface-border bg-surface-base shadow-2xl focus:outline-none sm:max-w-lg sm:rounded-2xl"
      >
        <div className="flex items-center justify-between border-b border-surface-border px-5 py-3">
          <h2 id="share-title" className="text-sm font-semibold text-foreground">
            Share this session
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-md p-1 text-neutral-500 hover:bg-surface-elevated hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-6 overflow-y-auto px-5 py-4">
          <section className="space-y-3">
            <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
              <Users className="h-3.5 w-3.5" aria-hidden="true" /> People
            </h3>
            <Field
              label="Members by email"
              hint="One per line or comma-separated. They can read the session and send messages in it."
            >
              {(control) => (
                <Textarea
                  {...control}
                  data-testid="share-emails"
                  rows={3}
                  value={emails}
                  onChange={(event) => setEmails(event.target.value)}
                  placeholder="alex@example.com"
                />
              )}
            </Field>
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                data-testid="share-org-wide"
                checked={orgWide}
                onChange={(event) => setOrgWide(event.target.checked)}
              />
              Everyone in the organization
            </label>
            <Button variant="primary" size="sm" data-testid="share-save" disabled={busy || !sharing} onClick={saveMembers}>
              Save
            </Button>
          </section>

          <section className="space-y-3">
            <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
              <Globe className="h-3.5 w-3.5" aria-hidden="true" /> Public link
            </h3>
            {sharing && !sharing.public_links_enabled && (
              <p className="text-xs text-neutral-500">
                Public links are turned off for this organization. An administrator can allow them under Settings →
                Organization → General.
              </p>
            )}
            {sharing?.public_links_enabled && !sharing.public_link && (
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Expires after" className="w-40">
                  {(control) => (
                    <Select {...control} value={days} onChange={(event) => setDays(event.target.value)}>
                      <option value="1">1 day</option>
                      <option value="7">7 days</option>
                      <option value="30">30 days</option>
                    </Select>
                  )}
                </Field>
                <Button size="sm" data-testid="share-create-link" disabled={busy} onClick={createLink}>
                  Create link
                </Button>
              </div>
            )}
            {sharing?.public_link && (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    data-testid="share-link"
                    aria-label="Public link"
                    value={linkUrl}
                    onFocus={(event) => event.currentTarget.select()}
                    className="h-9 min-w-0 flex-1 rounded-lg border border-surface-border bg-surface-card px-3 font-mono text-xs text-foreground"
                  />
                  <Button size="sm" onClick={copyLink} aria-label="Copy link">
                    {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copied ? "Copied" : "Copy"}
                  </Button>
                </div>
                <p className="text-xs text-neutral-500">
                  Anyone with this link can read the conversation (not the tools, files or terminal) until{" "}
                  {new Date(sharing.public_link.expires_at * 1000).toLocaleDateString()}.
                </p>
                <Button variant="danger" size="sm" data-testid="share-revoke-link" disabled={busy} onClick={revokeLink}>
                  <Link2Off className="h-3.5 w-3.5" aria-hidden="true" /> Revoke link
                </Button>
              </div>
            )}
          </section>

          <div aria-live="polite" className="min-h-4 text-xs">
            {error && <p className="text-rose-600 dark:text-rose-400">{error}</p>}
            {notice && <p className="text-neutral-600 dark:text-neutral-300">{notice}</p>}
          </div>
        </div>
      </div>
    </div>
  );
};
