"use client";

import React, { useState } from "react";
import { Field, Input, Textarea } from "../ui/Field";

interface KnowledgeIngestDialogProps {
  kbId: string;
  kbName: string;
  onClose: () => void;
  /** Called with the stored document's title and chunk count. */
  onIngested: (title: string, chunks: number) => void;
}

/** Add one document to a knowledge base: it is chunked and embedded on the server. */
export const KnowledgeIngestDialog: React.FC<KnowledgeIngestDialogProps> = ({ kbId, kbName, onClose, onIngested }) => {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !content.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/v1/knowledge/bases/${kbId}/documents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), content: content.trim() }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "The document was not added.");
      onIngested(String(body.title ?? title), Number(body.chunks_count ?? 1));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The document was not added.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="kb-ingest-title"
        className="w-full max-w-lg space-y-4 rounded-lg border border-border bg-surface p-5 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-border/60 pb-2">
          <h2 id="kb-ingest-title" className="text-xs font-bold text-foreground">
            Add a document to {kbName}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-xs text-neutral-400 hover:text-foreground">
            ✕
          </button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Title">
            {(control) => (
              <Input {...control} required placeholder="e.g. Deployment guide" value={title} onChange={(e) => setTitle(e.target.value)} />
            )}
          </Field>
          <Field label="Content (Markdown or plain text)">
            {(control) => (
              <Textarea {...control} required rows={8} value={content} onChange={(e) => setContent(e.target.value)} />
            )}
          </Field>
          {error && (
            <p role="alert" className="text-xs text-rose-500">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2 border-t border-border/60 pt-2">
            <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-xs text-foreground">
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-brand px-4 py-1.5 text-xs font-semibold text-white hover:bg-brand-hover disabled:opacity-50"
            >
              {busy ? "Chunking and embedding…" : "Add document"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
