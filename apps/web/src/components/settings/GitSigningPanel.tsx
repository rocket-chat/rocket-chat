"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useConfirm } from "../ui/ConfirmDialog";
import { AlertCircle, CheckCircle2, Copy, ExternalLink, KeyRound, Trash2 } from "lucide-react";
import { Button } from "../ui/Button";
import { Field, Input, Textarea } from "../ui/Field";

interface GitSigningStatus {
  available: boolean;
  mode: string;
  configured: boolean;
  fingerprint?: string | null;
  key_id?: string | null;
  identities?: string[];
  commit_email?: string | null;
  algorithm?: string | null;
  expires_at?: number | null;
  has_passphrase?: boolean;
  public_key?: string | null;
}

const MODE_NOTES: Record<string, string> = {
  github:
    "Your organization has commits created through GitHub's API, so GitHub signs them as the GitHub App and they show as verified. You do not need a key.",
  none: "Your organization does not sign commits.",
};

const ENABLE_KEYS =
  "Members can import their own GPG key once an administrator sets the commit signing mode to “member_gpg” (Organization > Git & GitHub).";

function formatFingerprint(fingerprint: string): string {
  return fingerprint.replace(/(.{4})/g, "$1 ").trim();
}

/** Import a GPG key so commits the agent makes as you are signed (verified on GitHub). */
export const GitSigningPanel: React.FC = () => {
  const [confirm, confirmDialog] = useConfirm();
  const [status, setStatus] = useState<GitSigningStatus | null>(null);
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [commitEmail, setCommitEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/v1/user/git-signing");
      if (res.ok) setStatus(await res.json());
    } catch {
      // The rest of the page still works without the signing section.
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (!status) return null;

  const handleImport = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch("/v1/user/git-signing", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          private_key: privateKey,
          passphrase,
          commit_email: commitEmail.trim() || undefined,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Could not import the key");
      setStatus(body);
      setPrivateKey("");
      setPassphrase("");
      setCommitEmail("");
      setNotice("Key imported. Upload its public key to GitHub, then turn on “Commit as me”.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not import the key");
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    if (!(await confirm({ title: "Remove your signing key?", message: "Commits will no longer be signed with it.", confirmLabel: "Remove key", tone: "danger" }))) return;
    setBusy(true);
    try {
      const res = await fetch("/v1/user/git-signing", { method: "DELETE" });
      if (res.ok) {
        setStatus(await res.json());
        setNotice(null);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = async () => {
    if (!status.public_key) return;
    try {
      await navigator.clipboard.writeText(status.public_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Copy failed; select the key and copy it manually.");
    }
  };

  const usesKeys = status.mode === "member_gpg";

  return (
    <section data-testid="git-signing" className="space-y-3 rounded-lg border border-border bg-surface-card p-4">
      {confirmDialog}
      <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
        <KeyRound className="h-4 w-4 text-brand" aria-hidden="true" />
        Commit signing
      </div>

      {!usesKeys && (
        <p data-testid="git-signing-mode-note" className="text-xs text-muted-foreground">
          {MODE_NOTES[status.mode] ?? "Commit signing is managed by your organization."} {ENABLE_KEYS}
        </p>
      )}

      {usesKeys && !status.configured && (
        <div data-testid="unsigned-commits-warning" className="flex items-start gap-2.5 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-700 dark:text-amber-300 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="space-y-0.5">
            <span className="font-semibold">Unsigned Commits Warning</span>
            <p className="text-[11px] leading-relaxed">
              Your git policy is set to GPG key signing, but you have no private key configured below. Commits pushed from sandboxes will be <strong>unsigned</strong> and may be rejected by GitHub repositories with branch protection requiring signed commits.
            </p>
          </div>
        </div>
      )}

      {usesKeys && (
        <>
          <p className="text-xs text-muted-foreground">
            Import a GPG key and choose to commit as yourself: commits the agent makes are then signed with it. Without a
            key, or without that choice, commits are created through GitHub&apos;s API and signed by GitHub as the GitHub
            App.
          </p>
          <p className="text-[11px] text-amber-700 dark:text-amber-300">
            The key is available to your sandbox while a turn runs, so use a key made only for signing commits.
          </p>

          {!status.available && (
            <p role="status" className="text-xs text-amber-700 dark:text-amber-300">
              This deployment cannot check GPG keys (GnuPG is not installed), so importing is unavailable.
            </p>
          )}

          {status.configured ? (
            <div data-testid="git-signing-key" className="space-y-2 rounded-lg border border-border/60 p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 font-medium text-foreground">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden="true" />
                  {status.algorithm} key {formatFingerprint(status.fingerprint ?? "").slice(-19)}
                </div>
                <Button size="sm" variant="danger" onClick={handleRemove} disabled={busy}>
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Remove key
                </Button>
              </div>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-muted-foreground">
                <dt>Fingerprint</dt>
                <dd className="break-all font-mono text-foreground">{formatFingerprint(status.fingerprint ?? "")}</dd>
                <dt>Commit e-mail</dt>
                <dd className="text-foreground">{status.commit_email}</dd>
                <dt>Identities</dt>
                <dd className="text-foreground">{(status.identities ?? []).join(", ")}</dd>
                <dt>Expires</dt>
                <dd className="text-foreground">
                  {status.expires_at ? new Date(status.expires_at * 1000).toLocaleDateString() : "never"}
                </dd>
              </dl>
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-muted-foreground">
                    Public key: add it to GitHub under Settings → SSH and GPG keys, with {status.commit_email} verified on
                    your account, or commits will not show as verified.
                  </span>
                  <span className="flex gap-2">
                    <Button size="sm" onClick={handleCopy}>
                      <Copy className="h-3.5 w-3.5" aria-hidden="true" /> {copied ? "Copied" : "Copy"}
                    </Button>
                    <a
                      href="https://github.com/settings/keys"
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-surface-border px-2.5 text-xs text-brand hover:bg-surface-elevated"
                    >
                      GitHub keys <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  </span>
                </div>
                <pre
                  data-testid="git-signing-public-key"
                  className="max-h-32 overflow-auto rounded-lg border border-border/60 bg-surface-base p-2 font-mono text-[10px]"
                >
                  {status.public_key}
                </pre>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <Field label="GPG private key" hint="Armored: gpg --armor --export-secret-keys <key id>">
                {(control) => (
                  <Textarea
                    {...control}
                    data-testid="git-signing-private-key"
                    rows={5}
                    className="font-mono text-xs"
                    placeholder="-----BEGIN PGP PRIVATE KEY BLOCK-----"
                    value={privateKey}
                    onChange={(e) => setPrivateKey(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                  />
                )}
              </Field>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Passphrase" hint="Leave empty if the key has none.">
                  {(control) => (
                    <Input
                      {...control}
                      data-testid="git-signing-passphrase"
                      type="password"
                      autoComplete="off"
                      value={passphrase}
                      onChange={(e) => setPassphrase(e.target.value)}
                    />
                  )}
                </Field>
                <Field label="Commit e-mail" hint="Must be one of the key's identities; optional when it has just one.">
                  {(control) => (
                    <Input
                      {...control}
                      data-testid="git-signing-email"
                      type="email"
                      value={commitEmail}
                      onChange={(e) => setCommitEmail(e.target.value)}
                    />
                  )}
                </Field>
              </div>
              <Button
                variant="primary"
                size="sm"
                data-testid="git-signing-import"
                onClick={handleImport}
                disabled={busy || !privateKey.trim() || !status.available}
              >
                {busy ? "Checking the key…" : "Import key"}
              </Button>
            </div>
          )}

          {error && (
            <p role="alert" data-testid="git-signing-error" className="flex items-center gap-1.5 text-xs text-rose-600 dark:text-rose-400">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> {error}
            </p>
          )}
          {notice && (
            <p role="status" className="text-xs text-emerald-700 dark:text-emerald-300">
              {notice}
            </p>
          )}

        </>
      )}
    </section>
  );
};
