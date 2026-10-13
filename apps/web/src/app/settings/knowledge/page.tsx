"use client";

import { Button } from "../../../components/ui/Button";
import { PageHeader } from "../../../components/ui/PageHeader";

import { Field, Input, Textarea } from "../../../components/ui/Field";
import React, { useState, useEffect, useCallback } from "react";
import { KnowledgeBaseConfig } from "../../../types/mission";
import {
  Database,
  Plus,
  Search,
  UploadCloud,
  CheckCircle2,
  AlertCircle,
  Trash2,
} from "lucide-react";
import { useSession } from "../../../components/AuthProvider";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../components/settings/ScopedSettingsSection";

interface ChunkResult {
  id: string;
  score: number;
  title: string;
  content: string;
}

export default function KnowledgeSettingsPage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseConfig[]>([]);
  const [selectedKbId, setSelectedKbId] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<ChunkResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  // Modal states
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createScope, setCreateScope] = useState<"user" | "org">("user");
  const [newKbName, setNewKbName] = useState("");
  const [newKbDesc, setNewKbDesc] = useState("");
  const [newKbIndex, setNewKbIndex] = useState("");

  const [showIngestModal, setShowIngestModal] = useState(false);
  const [docTitle, setDocTitle] = useState("");
  const [docContent, setDocContent] = useState("");
  const [ingesting, setIngesting] = useState(false);

  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const fetchKnowledgeBases = useCallback(async () => {
    try {
      const res = await fetch("/v1/knowledge/bases");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          setKnowledgeBases(data);
          if (data.length > 0 && !selectedKbId) {
            setSelectedKbId(data[0].id);
          }
        }
      }
    } catch (err) {
      console.warn("Failed fetching knowledge bases:", err);
    }
  }, [selectedKbId]);

  useEffect(() => {
    fetchKnowledgeBases();
  }, [fetchKnowledgeBases]);

  const userKbs = knowledgeBases.filter((k) => k.scope === "user" || !k.scope || k.user_id != null);
  const orgKbs = knowledgeBases.filter((k) => k.scope === "org" && k.user_id == null);

  const handleOpenCreateModal = (scope: "user" | "org") => {
    setCreateScope(scope);
    setNewKbName("");
    setNewKbDesc("");
    setNewKbIndex("");
    setShowCreateModal(true);
  };

  const handleCreateKb = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKbName.trim()) return;
    setErrorBanner(null);
    try {
      const res = await fetch("/v1/knowledge/bases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newKbName.trim(),
          description: newKbDesc.trim() || undefined,
          scope: createScope,
          vector_index: newKbIndex.trim() || undefined,
          embedding_model: "text-embedding-3-small",
          hybrid_search: true,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to create knowledge base");
      }
      const created = await res.json();
      setKnowledgeBases((prev) => [...prev, created]);
      setSelectedKbId(created.id);
      setShowCreateModal(false);
      setSuccessBanner(`Created vector knowledge base '${created.name}' (${createScope} scope)`);
      setTimeout(() => setSuccessBanner(null), 3500);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error creating knowledge base";
      setErrorBanner(msg);
      setTimeout(() => setErrorBanner(null), 6000);
    }
  };

  const handleDeleteKb = async (id: string, name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Are you sure you want to delete knowledge base '${name}'?`)) {
      return;
    }
    try {
      const res = await fetch(`/v1/knowledge/bases/${id}`, { method: "DELETE" });
      if (res.ok) {
        setKnowledgeBases((prev) => prev.filter((k) => k.id !== id));
        if (selectedKbId === id) {
          const remaining = knowledgeBases.filter((k) => k.id !== id);
          setSelectedKbId(remaining[0]?.id || "");
        }
        setSuccessBanner("Knowledge base removed");
        setTimeout(() => setSuccessBanner(null), 3000);
      } else {
        const err = await res.json().catch(() => ({}));
        setErrorBanner(err.detail || "Failed to delete knowledge base");
      }
    } catch {
      setErrorBanner("Failed to delete knowledge base");
    }
  };

  const handleIngestDocument = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedKbId || !docTitle.trim() || !docContent.trim()) return;
    setIngesting(true);
    setErrorBanner(null);
    try {
      const res = await fetch(`/v1/knowledge/bases/${selectedKbId}/documents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: docTitle.trim(),
          content: docContent.trim(),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to ingest document");
      }
      const data = await res.json();
      setKnowledgeBases((prev) =>
        prev.map((k) =>
          k.id === selectedKbId ? { ...k, documents_count: (k.documents_count || 0) + 1 } : k
        )
      );
      setShowIngestModal(false);
      setDocTitle("");
      setDocContent("");
      setSuccessBanner(`Ingested '${data.title}' into ${data.chunks_count || 1} vector chunks`);
      setTimeout(() => setSuccessBanner(null), 3500);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error ingesting document";
      setErrorBanner(msg);
      setTimeout(() => setErrorBanner(null), 6000);
    } finally {
      setIngesting(false);
    }
  };

  const handleQuery = async () => {
    if (!searchQuery.trim() || !selectedKbId) return;
    setIsSearching(true);
    setErrorBanner(null);
    try {
      const res = await fetch(`/v1/knowledge/bases/${selectedKbId}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: searchQuery.trim(), limit: 4 }),
      });
      if (res.ok) {
        const data = await res.json();
        setSearchResults(data.results || []);
      } else {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Search query failed");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Vector query failed";
      setErrorBanner(msg);
    } finally {
      setIsSearching(false);
    }
  };

  const currentKb = knowledgeBases.find((k) => k.id === selectedKbId);

  const renderKbCard = (kb: KnowledgeBaseConfig, canManage: boolean) => {
    const isSelected = selectedKbId === kb.id;
    return (
      <div
        key={kb.id}
        onClick={() => setSelectedKbId(kb.id)}
        className={`p-3 rounded-lg border transition-all cursor-pointer flex items-center justify-between gap-3 ${
          isSelected
            ? "border-cyan bg-cyan/10 shadow-sm shadow-cyan/5"
            : "border-border bg-surface-card hover:border-brand/30"
        }`}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-foreground truncate">
              {kb.name}
            </span>
            <span className="px-1.5 py-0.2 rounded-lg text-[9px] bg-surface border border-border text-neutral-400">
              {kb.documents_count ?? 0} docs
            </span>
          </div>
          {kb.description && (
            <p className="text-[10px] text-muted-foreground truncate mt-0.5">
              {kb.description}
            </p>
          )}
        </div>

        {canManage && (
          <button
            type="button"
            onClick={(e) => handleDeleteKb(kb.id, kb.name, e)}
            className="p-1 rounded-lg hover:bg-rose-950/40 text-neutral-500 hover:text-rose-400 transition-colors shrink-0"
            title="Delete collection"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      {/* Page Header */}
      <PageHeader
        title="Knowledge bases"
        icon={<Database className="h-5 w-5" aria-hidden="true" />}
        description="Personal and organization vector collections, document ingestion, and hybrid dense-sparse retrieval."
      />

      {errorBanner && (
        <div className="flex items-center gap-2 p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorBanner}</span>
        </div>
      )}

      {successBanner && (
        <div className="flex items-center gap-2 p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successBanner}</span>
        </div>
      )}

      {/* Main Grid: Collections Left, Details/Search Right */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Two-Tier Knowledge Collections */}
        <div className="space-y-6">
          <UserScopeSection
            title="Personal Collections"
            description="Private vector documents accessible to your sessions."
            action={
              <Button size="sm" variant="secondary" onClick={() => handleOpenCreateModal("user")}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                New collection
              </Button>
            }
          >
            {userKbs.length > 0 ? (
              <div className="space-y-2">
                {userKbs.map((kb) => renderKbCard(kb, true))}
              </div>
            ) : (
              <div className="p-4 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
                No personal collections yet.
              </div>
            )}
          </UserScopeSection>

          <ScopeDivider label="Organization Scope" />

          <OrgScopeSection
            title="Organization Collections"
            description="Shared knowledge collections available across the entire workspace."
            action={
              <Button size="sm" variant="secondary" onClick={() => handleOpenCreateModal("org")}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                New org collection
              </Button>
            }
          >
            {orgKbs.length > 0 ? (
              <div className="space-y-2">
                {orgKbs.map((kb) => renderKbCard(kb, isAdmin))}
              </div>
            ) : (
              <div className="p-4 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
                No organization collections registered.
              </div>
            )}
          </OrgScopeSection>
        </div>

        {/* Right Column: Collection Inspector & Query Engine */}
        <div className="lg:col-span-2 space-y-4">
          {currentKb ? (
            <div className="p-5 rounded-lg border border-border bg-surface-card space-y-4">
              <div className="flex items-center justify-between border-b border-border/60 pb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold text-foreground">
                      {currentKb.name}
                    </h2>
                    <span className="px-1.5 py-0.5 rounded-lg text-[9px] bg-surface border border-border text-neutral-400">
                      {currentKb.scope || "user"}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {currentKb.description || "Vector knowledge repository for RAG context injection."}
                  </p>
                </div>

                <button
                  type="button"
                  onClick={() => setShowIngestModal(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-cyan/40 bg-cyan/10 hover:bg-cyan/20 text-cyan text-xs font-semibold transition-all cursor-pointer"
                >
                  <UploadCloud className="w-3.5 h-3.5" />
                  <span>Ingest document</span>
                </button>
              </div>

              {/* Live Search & Hybrid Test */}
              <div className="space-y-3">
                <div className="text-[10px] text-muted-foreground font-semibold">
                  Test Retrieval & Similarity Search:
                </div>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-neutral-500" />
                    <input
                      type="text"
                      placeholder="Enter query to test cosine similarity search..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && handleQuery()}
                      className="w-full pl-9 pr-3 py-1.5 rounded-lg border border-border bg-void text-xs text-foreground focus:outline-none focus:border-cyan"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={handleQuery}
                    disabled={isSearching || !searchQuery.trim()}
                    className="px-3 py-1.5 rounded-lg border border-border bg-surface-elevated hover:bg-surface-elevated/80 text-xs text-neutral-300 transition-all cursor-pointer disabled:opacity-50"
                  >
                    {isSearching ? "SEARCHING..." : "SEARCH"}
                  </button>
                </div>

                {searchResults.length > 0 && (
                  <div className="space-y-2 pt-2">
                    <div className="text-[10px] text-muted-foreground font-semibold">
                      Matching Vector Chunks ({searchResults.length}):
                    </div>
                    {searchResults.map((res) => (
                      <div
                        key={res.id}
                        className="p-3 rounded-lg border border-border/80 bg-surface text-xs space-y-1"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-brand">{res.title}</span>
                          <span className="text-[10px] text-cyan">
                            Score: {(res.score * 100).toFixed(1)}%
                          </span>
                        </div>
                        <p className="text-[11px] text-neutral-300 whitespace-pre-wrap leading-relaxed">
                          {res.content}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="p-8 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
              Select or create a knowledge collection to view details and ingest documents.
            </div>
          )}
        </div>
      </div>

      {/* Modal: Create Collection */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="w-full max-w-md p-5 rounded-lg border border-border bg-surface space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-border/60 pb-2">
              <span className="text-xs font-bold text-cyan">
                Create {createScope === "org" ? "Organization" : "Personal"} Knowledge Base
              </span>
              <button
                type="button"
                onClick={() => setShowCreateModal(false)}
                className="text-neutral-400 hover:text-white text-xs cursor-pointer"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateKb} className="space-y-3">
              <Field label="Collection Name *">
                {(control) => (
                  <Input {...control}
                    placeholder="e.g. Architecture RFCs"
                    value={newKbName}
                    onChange={(e) => setNewKbName(e.target.value)}
                    required
                  />
                )}
              </Field>

              <Field label="Description">
                {(control) => (
                  <Input {...control}
                    placeholder="Summary of indexed documents"
                    value={newKbDesc}
                    onChange={(e) => setNewKbDesc(e.target.value)}
                  />
                )}
              </Field>

              <div className="flex justify-end gap-2 pt-2 border-t border-border/60">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-neutral-400 hover:text-white cursor-pointer"
                >
                  CANCEL
                </button>
                <button
                  type="submit"
                  className="px-4 py-1.5 rounded-lg border border-cyan/40 bg-cyan text-slate-950 hover:bg-cyan/90 text-xs font-semibold transition-all cursor-pointer"
                >
                  CREATE COLLECTION
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Ingest Document */}
      {showIngestModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="w-full max-w-lg p-5 rounded-lg border border-border bg-surface space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-border/60 pb-2">
              <span className="text-xs font-bold text-cyan">
                Ingest Document to {currentKb?.name}
              </span>
              <button
                type="button"
                onClick={() => setShowIngestModal(false)}
                className="text-neutral-400 hover:text-white text-xs cursor-pointer"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleIngestDocument} className="space-y-3">
              <Field label="Document Title *">
                {(control) => (
                  <Input {...control}
                    placeholder="e.g. Deployment Guide v2"
                    value={docTitle}
                    onChange={(e) => setDocTitle(e.target.value)}
                    required
                  />
                )}
              </Field>

              <Field label="Content (Markdown / Plain Text) *">
                {(control) => (
                  <Textarea {...control}
                    rows={8}
                    placeholder="Paste documentation text to be chunked and embedded..."
                    value={docContent}
                    onChange={(e) => setDocContent(e.target.value)}
                    required
                  />
                )}
              </Field>

              <div className="flex justify-end gap-2 pt-2 border-t border-border/60">
                <button
                  type="button"
                  onClick={() => setShowIngestModal(false)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-neutral-400 hover:text-white cursor-pointer"
                >
                  CANCEL
                </button>
                <button
                  type="submit"
                  disabled={ingesting}
                  className="px-4 py-1.5 rounded-lg border border-cyan/40 bg-cyan text-slate-950 hover:bg-cyan/90 text-xs font-semibold transition-all cursor-pointer disabled:opacity-50"
                >
                  {ingesting ? "CHUNKING & EMBEDDING..." : "INGEST DOCUMENT"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
