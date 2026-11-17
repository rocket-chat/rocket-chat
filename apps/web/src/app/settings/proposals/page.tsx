"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Check, GitPullRequestDraft, X } from "lucide-react";
import { Badge, type BadgeTone } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field, Select, Textarea } from "../../../components/ui/Field";
import { PageHeader } from "../../../components/ui/PageHeader";

type Status = "pending" | "approved" | "rejected" | "all";

interface Proposal {
  id: string;
  agent_id: string;
  target_kind: "instructions" | "skill";
  target_name: string;
  summary: string;
  rationale: string;
  status: Exclude<Status, "all">;
  requested_by: string;
  created_at: number;
  decided_by: string | null;
  decision_note: string;
  base_content: string;
  proposed_content: string;
}

const TONE: Record<Proposal["status"], BadgeTone> = {
  pending: "warning",
  approved: "success",
  rejected: "neutral",
};

function when(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString();
}

/** Changes agents proposed to their instructions and skills, for the people who may edit them. */
export default function ProposalsPage() {
  const [status, setStatus] = useState<Status>("pending");
  const [rows, setRows] = useState<Proposal[]>([]);
  const [open, setOpen] = useState<Proposal | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const res = await fetch(`/v1/proposals?status=${status}`);
    if (res.ok) setRows(((await res.json()) as { proposals: Proposal[] }).proposals);
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const select = async (id: string) => {
    setError(null);
    const res = await fetch(`/v1/proposals/${id}`);
    if (!res.ok) return;
    const found = (await res.json()) as Proposal;
    setOpen(found);
    setDraft(found.proposed_content);
    setNote("");
  };

  const decide = async (action: "approve" | "reject") => {
    if (!open) return;
    setBusy(true);
    setError(null);
    const edited = draft !== open.proposed_content;
    const body = action === "approve" ? { note, ...(edited ? { content: draft } : {}) } : { note };
    const res = await fetch(`/v1/proposals/${open.id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setBusy(false);
    if (!res.ok) {
      const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail;
      setError(detail ?? "The decision was not saved.");
      return;
    }
    setOpen(null);
    await load();
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      <PageHeader
        title="Proposals"
        icon={<GitPullRequestDraft className="h-5 w-5" aria-hidden="true" />}
        description="Changes agents propose to their own instructions and skills. Nothing changes until you approve it; you see the proposals of the agents and skills you may edit."
        actions={
          <Field label="Status" className="w-40">
            {(control) => (
              <Select
                {...control}
                data-testid="proposals-status"
                value={status}
                onChange={(e) => setStatus(e.target.value as Status)}
              >
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="all">All</option>
              </Select>
            )}
          </Field>
        }
      />

      {rows.length === 0 ? (
        <EmptyState
          icon={<GitPullRequestDraft className="h-5 w-5" aria-hidden="true" />}
          title="No proposals"
          description="Turn on “Propose changes” in an agent's Sandbox section to let it suggest edits here."
        />
      ) : (
        <ul className="space-y-2" data-testid="proposals-list">
          {rows.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => void select(p.id)}
                className="w-full rounded-lg border border-border bg-surface-card p-3.5 text-left hover:border-brand/30"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={TONE[p.status]}>{p.status}</Badge>
                  <span className="text-sm font-medium text-foreground">{p.summary}</span>
                </div>
                <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
                  {p.target_kind === "skill" ? `Skill “${p.target_name}”` : `Instructions of ${p.target_name}`} ·{" "}
                  {when(p.created_at)}
                  {p.requested_by ? ` · while answering ${p.requested_by}` : ""}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}

      {open && (
        <section
          aria-label="Proposal"
          data-testid="proposal-detail"
          className="space-y-4 rounded-lg border border-border bg-surface-card p-4"
        >
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-foreground">{open.summary}</h2>
            <p className="whitespace-pre-wrap text-sm text-neutral-700 dark:text-neutral-300">{open.rationale}</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Current text">
              {(control) => (
                <Textarea {...control} readOnly rows={14} value={open.base_content} className="font-mono text-xs" />
              )}
            </Field>
            <Field
              label={open.status === "pending" ? "Proposed text (you may edit it before approving)" : "Saved text"}
            >
              {(control) => (
                <Textarea
                  {...control}
                  data-testid="proposal-text"
                  readOnly={open.status !== "pending"}
                  rows={14}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  className="font-mono text-xs"
                />
              )}
            </Field>
          </div>
          {open.status === "pending" ? (
            <>
              <Field label="Note for the agent" hint="The agent reads it with list_my_proposals.">
                {(control) => (
                  <Textarea
                    {...control}
                    data-testid="proposal-note"
                    rows={2}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                )}
              </Field>
              {error && (
                <p role="alert" className="text-sm text-red-700 dark:text-red-400">
                  {error}
                </p>
              )}
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  disabled={busy}
                  data-testid="proposal-approve"
                  onClick={() => void decide("approve")}
                >
                  <Check className="h-4 w-4" aria-hidden="true" /> Approve
                </Button>
                <Button disabled={busy} data-testid="proposal-reject" onClick={() => void decide("reject")}>
                  <X className="h-4 w-4" aria-hidden="true" /> Reject
                </Button>
              </div>
            </>
          ) : (
            open.decision_note && (
              <p className="text-sm text-neutral-700 dark:text-neutral-300">Note: {open.decision_note}</p>
            )
          )}
        </section>
      )}
    </div>
  );
}
