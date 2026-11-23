"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, ExternalLink, GitBranch, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Badge, type BadgeTone } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { useConfirm } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field, Input, Select } from "../../../components/ui/Field";
import { PageHeader } from "../../../components/ui/PageHeader";
import { OwnerBadge, OwnerSelect } from "../../../components/settings/Owner";
import { useOwnedSections } from "../../../lib/useOwnedSections";
import { listSources, sendJson, shortCommit, type ExternalSource } from "../../../lib/external-sources";
import { ownerFields, type OwnerKey } from "../../../lib/ownership";

const STATUS_TONE: Record<ExternalSource["status"], BadgeTone> = { ok: "success", error: "danger", pending: "neutral" };
const STATUS_LABEL: Record<ExternalSource["status"], string> = { ok: "In sync", error: "Read failed", pending: "Not read yet" };

function when(epoch: number | null): string {
  return epoch ? new Date(epoch * 1000).toLocaleString() : "never";
}

interface ConnectFormProps {
  creatable: OwnerKey[];
  startOwner: OwnerKey;
  onConnected: (source: ExternalSource) => void;
  onCancel: () => void;
}

/** Connect a repository: which one, which branch, for which owner, read with what. */
const ConnectForm: React.FC<ConnectFormProps> = ({ creatable, startOwner, onConnected, onCancel }) => {
  const [repository, setRepository] = useState("");
  const [ref, setRef] = useState("main");
  const [name, setName] = useState("");
  const [owner, setOwner] = useState<OwnerKey>(startOwner);
  const [auth, setAuth] = useState<"app" | "member">("app");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const source = await sendJson<ExternalSource>("/v1/external-sources", "POST", {
        repository: repository.trim(),
        ref: ref.trim() || "main",
        name: name.trim(),
        auth: owner === "me" ? auth : "app",
        ...ownerFields(owner),
      });
      onConnected(source);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The source was not connected.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 rounded-lg border border-brand/40 bg-surface p-5" data-testid="source-connect-form">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Repository *" hint="owner/name or its GitHub URL">
          {(control) => (
            <Input {...control} data-testid="source-repository" placeholder="acme/agent-skills" value={repository} onChange={(e) => setRepository(e.target.value)} />
          )}
        </Field>
        <Field label="Branch" hint="The branch the skills follow">
          {(control) => <Input {...control} data-testid="source-ref" value={ref} onChange={(e) => setRef(e.target.value)} />}
        </Field>
        <Field label="Name" hint="Shown on the skills it feeds; the repository when empty">
          {(control) => <Input {...control} data-testid="source-name" value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <OwnerSelect testId="source-owner" value={owner} options={creatable} onChange={setOwner} label="Owner (who can import from it)" />
      </div>
      {owner === "me" && (
        <Field
          label="Read it with"
          hint="With your own account, its skills only go to your personal skills, and your linked GitHub account must be able to read the repository."
        >
          {(control) => (
            <Select {...control} data-testid="source-auth" value={auth} onChange={(e) => setAuth(e.target.value as "app" | "member")} className="max-w-xs">
              <option value="app">The organization&apos;s GitHub App</option>
              <option value="member">My linked GitHub account</option>
            </Select>
          )}
        </Field>
      )}
      {error && <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">{error}</p>}
      <div className="flex justify-end gap-2 border-t border-border/60 pt-3">
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button size="sm" variant="primary" data-testid="source-connect-submit" disabled={busy || !repository.trim()} onClick={() => void submit()}>
          Connect and read
        </Button>
      </div>
    </div>
  );
};

const SourceCard: React.FC<{
  source: ExternalSource;
  onSync: () => void;
  onDelete: () => void;
  busy: boolean;
}> = ({ source, onSync, onDelete, busy }) => (
  <li className="space-y-2 rounded-lg border border-border bg-surface-card p-4" data-testid="source-card" data-source-id={source.id}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <GitBranch className="h-4 w-4 text-brand" aria-hidden="true" />
          <span className="text-sm font-semibold text-foreground">{source.name}</span>
          <OwnerBadge owned={source} />
          <Badge tone={STATUS_TONE[source.status]} data-testid="source-status">{STATUS_LABEL[source.status]}</Badge>
          {source.auth === "member" && <Badge>Your GitHub account</Badge>}
        </div>
        <a href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-brand hover:underline">
          {source.repository} · {source.ref}
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
        <p className="text-xs text-muted-foreground">
          {source.skill_count} skills{source.has_manifest ? ` in ${source.plugin_count} plugins` : ""} · {source.imported_count} imported ·
          read {when(source.last_synced_at)}
          {source.last_commit ? ` at ${shortCommit(source.last_commit)}` : ""}
        </p>
      </div>
      {source.can_manage && (
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" data-testid="source-sync" disabled={busy} onClick={onSync}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Synchronise
          </Button>
          <Button size="sm" variant="danger" aria-label={`Disconnect ${source.name}`} onClick={onDelete}>
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      )}
    </div>
    {source.last_error && (
      <p className="flex items-start gap-1.5 text-xs text-rose-600 dark:text-rose-400" data-testid="source-error">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {source.last_error}
      </p>
    )}
    {source.notes.map((note) => (
      <p key={note} className="text-[11px] text-amber-700 dark:text-amber-400">{note}</p>
    ))}
  </li>
);

/** Repositories skills are imported from and kept in sync with. */
export default function SourcesPage() {
  const [confirm, confirmDialog] = useConfirm();
  const [sources, setSources] = useState<ExternalSource[]>([]);
  const [connecting, setConnecting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const sections = useOwnedSections(sources);

  const load = useCallback(async () => setSources(await listSources()), []);

  useEffect(() => {
    void load();
  }, [load]);

  const sync = async (source: ExternalSource) => {
    setBusy(source.id);
    try {
      const result = await sendJson<{ updated: number; missing: number; error: string }>(`/v1/external-sources/${source.id}/sync`, "POST");
      setBanner(result.error ? `Read failed: ${result.error}` : `Synchronised ${source.name}: ${result.updated} updated, ${result.missing} no longer in the repository.`);
      await load();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "The source was not synchronised.");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (source: ExternalSource) => {
    const ok = await confirm({
      title: `Disconnect “${source.name}”?`,
      message: "Its skills stay, no longer synchronised: they can then be edited here.",
      confirmLabel: "Disconnect",
      tone: "danger",
    });
    if (!ok) return;
    await fetch(`/v1/external-sources/${source.id}`, { method: "DELETE" });
    await load();
  };

  const visible = [...(sections.mine ?? []), ...sections.teamSections.flatMap((t) => t.items), ...(sections.org ?? [])];

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-8">
      {confirmDialog}
      <PageHeader
        title="Sources"
        icon={<GitBranch className="h-5 w-5" aria-hidden="true" />}
        description="Repositories skills are imported from. The repository stays their source of truth: they follow it on every push, and a change to one becomes a pull request."
        actions={
          sections.creatable.length > 0 && (
            <Button size="sm" variant="secondary" data-testid="source-connect" onClick={() => setConnecting(true)}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Connect a repository
            </Button>
          )
        }
      />
      {banner && (
        <p className="rounded-lg border border-border bg-surface-elevated p-3 text-xs text-foreground" role="status" data-testid="source-banner">
          {banner}
        </p>
      )}
      {connecting && sections.startOwner && (
        <ConnectForm
          creatable={sections.creatable}
          startOwner={sections.startOwner}
          onCancel={() => setConnecting(false)}
          onConnected={(source) => {
            setConnecting(false);
            setBanner(source.status === "ok" ? `Connected ${source.name}: ${source.skill_count} skills found.` : `Connected ${source.name}, but it could not be read.`);
            void load();
          }}
        />
      )}
      {visible.length === 0 ? (
        <EmptyState
          icon={<GitBranch className="h-6 w-6" aria-hidden="true" />}
          title="No repository connected"
          description="Connect a repository of skills (folders with a SKILL.md, grouped by plugin when it has a marketplace.json); then import them from the Skills page."
        />
      ) : (
        <ul className="space-y-3">
          {visible.map((source) => (
            <SourceCard key={source.id} source={source} busy={busy === source.id} onSync={() => void sync(source)} onDelete={() => void remove(source)} />
          ))}
        </ul>
      )}
    </div>
  );
}
