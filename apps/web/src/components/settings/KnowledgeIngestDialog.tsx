"use client";

import React, { useState } from "react";
import { Field, Input, Select, Textarea } from "../ui/Field";

interface KnowledgeIngestDialogProps {
  kbId: string;
  kbName: string;
  onClose: () => void;
  /** Called with the stored document's title (or how many were imported) and chunk count. */
  onIngested: (title: string, chunks: number) => void;
}

type Source = "text" | "url" | "site" | "pdf" | "repository";

const SOURCE_LABELS: Record<Source, string> = {
  text: "Text I paste",
  url: "A web page (or a PDF link)",
  site: "A documentation site (up to 25 pages under the address)",
  pdf: "A PDF file",
  repository: "A GitHub repository's docs folder",
};

async function fileAsBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Add documents to a knowledge base: they are chunked and embedded on the server. */
export const KnowledgeIngestDialog: React.FC<KnowledgeIngestDialogProps> = ({ kbId, kbName, onClose, onIngested }) => {
  const [source, setSource] = useState<Source>("text");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [url, setUrl] = useState("");
  const [repository, setRepository] = useState("");
  const [path, setPath] = useState("docs");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const request = async (): Promise<Response> => {
    if (source === "text") {
      return fetch(`/v1/knowledge/bases/${kbId}/documents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), content: content.trim() }),
      });
    }
    const body: Record<string, unknown> = { kind: source };
    if (source === "url" || source === "site") body.url = url.trim();
    if (source === "repository") Object.assign(body, { repository: repository.trim(), path: path.trim() });
    if (source === "pdf" && file) Object.assign(body, { title: file.name, content_base64: await fileAsBase64(file) });
    return fetch(`/v1/knowledge/bases/${kbId}/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await request();
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Nothing was added.");
      if (Array.isArray(body.documents)) {
        const chunks = body.documents.reduce((sum: number, d: { chunk_count?: number }) => sum + Number(d.chunk_count ?? 0), 0);
        onIngested(`${body.documents.length} document${body.documents.length === 1 ? "" : "s"}`, chunks);
      } else {
        onIngested(String(body.title ?? title), Number(body.chunk_count ?? 1));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Nothing was added.");
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
            Add to {kbName}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-xs text-neutral-400 hover:text-foreground">
            ✕
          </button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Source">
            {(control) => (
              <Select {...control} value={source} onChange={(e) => setSource(e.target.value as Source)} data-testid="kb-import-source">
                {(Object.keys(SOURCE_LABELS) as Source[]).map((key) => (
                  <option key={key} value={key}>
                    {SOURCE_LABELS[key]}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {source === "text" && (
            <>
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
            </>
          )}
          {(source === "url" || source === "site") && (
            <Field label="Address" hint={source === "site" ? "Pages under this address are followed, 25 at most." : undefined}>
              {(control) => (
                <Input {...control} required type="url" placeholder="https://docs.example.com/" value={url} onChange={(e) => setUrl(e.target.value)} />
              )}
            </Field>
          )}
          {source === "pdf" && (
            <Field label="PDF file" hint="Its text is imported; a scanned PDF needs OCR first.">
              {(control) => (
                <input
                  {...control}
                  required
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="text-xs text-foreground"
                />
              )}
            </Field>
          )}
          {source === "repository" && (
            <>
              <Field label="Repository">
                {(control) => (
                  <Input {...control} required placeholder="owner/name" value={repository} onChange={(e) => setRepository(e.target.value)} />
                )}
              </Field>
              <Field label="Folder" hint="Markdown, reStructuredText and text files under it (200 at most).">
                {(control) => <Input {...control} value={path} onChange={(e) => setPath(e.target.value)} />}
              </Field>
            </>
          )}
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
              {busy ? "Importing and embedding…" : "Add"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
