"use client";

import React, { useState } from "react";
import { KnowledgeBaseConfig } from "../../../types/mission";
import { MarkdownRenderer } from "../../../components/MarkdownRenderer";
import {
  Database,
  Plus,
  Search,
  FileText,
  UploadCloud,
} from "lucide-react";

export default function KnowledgeSettingsPage() {
  const [knowledgeBases] = useState<KnowledgeBaseConfig[]>([
    {
      id: "kb-codebase-architecture",
      name: "Platform Architecture & RFCs",
      vector_index: "pgvector_hnsw_platform_docs",
      documents_count: 42,
      embedding_model: "text-embedding-3-small (1536d)",
      hybrid_search: true,
    },
    {
      id: "kb-security-policies",
      name: "Security, RLS & Compliance Rules",
      vector_index: "pgvector_security_vetting",
      documents_count: 18,
      embedding_model: "text-embedding-3-small (1536d)",
      hybrid_search: false,
    },
    {
      id: "kb-api-specifications",
      name: "FastAPI & OpenAPI Contracts",
      vector_index: "pgvector_openapi_specs",
      documents_count: 29,
      embedding_model: "text-embedding-3-small (1536d)",
      hybrid_search: true,
    },
  ]);

  const [selectedKbId, setSelectedKbId] = useState<string>("kb-codebase-architecture");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults] = useState<
    Array<{ id: string; score: number; title: string; content: string }>
  >([
    {
      id: "chunk-101",
      score: 0.942,
      title: "doc/architecture/sandbox-lifecycle.md",
      content: `### Sandbox Volume Isolation & Lifecycle
Each mission session provisions an isolated workspace volume via the configured **SandboxDriverProtocol**:
- In **Docker driver mode**, a named Docker volume \`rocket-sandbox-{session_id}\` is mounted to \`/workspace\`.
- In **K8s driver mode**, a dynamic PersistentVolumeClaim (\`ReadWriteOnce\`) is scheduled.

\`\`\`python
# Zero-compute hibernation maintains storage volume integrity
await driver.hibernate_sandbox(session_id)
\`\`\``,
    },
    {
      id: "chunk-102",
      score: 0.887,
      title: "specifications/interfaces/llm.py",
      content: `### Streaming Protocol Contract
\`LLMGatewayProtocol\` guarantees asynchronous generator yields with granular token tagging:
| Field | Type | Description |
|---|---|---|
| \`text\` | str | Raw text completion delta |
| \`reasoning_delta\` | str | Extracted chain-of-thought tokens |
| \`tool_calls\` | list | Structured function calls |`,
    },
  ]);

  const currentKb = knowledgeBases.find((k) => k.id === selectedKbId) || knowledgeBases[0];

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold font-mono tracking-wide text-foreground flex items-center gap-2">
            <Database className="w-5 h-5 text-cyan" />
            PGVECTOR KNOWLEDGE BASES & RAG ENGINE
          </h1>
          <p className="text-xs font-mono text-muted-foreground mt-1">
            Configure vector collections, HNSW indexing parameters, hybrid dense-sparse retrieval, and document ingestion.
          </p>
        </div>

        <button
          type="button"
          onClick={() => alert("Index creation dialog")}
          className="flex items-center gap-2 px-3 py-2 rounded border border-cyan/40 bg-cyan/10 hover:bg-cyan/20 text-cyan text-xs font-mono font-semibold transition-all shadow-sm shadow-cyan/5 cursor-pointer"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>NEW VECTOR COLLECTION</span>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Collections list */}
        <div className="space-y-3">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold px-1">
            ACTIVE VECTOR INDICES ({knowledgeBases.length})
          </div>

          <div className="space-y-2">
            {knowledgeBases.map((kb) => {
              const isSelected = kb.id === selectedKbId;
              return (
                <div
                  key={kb.id}
                  onClick={() => setSelectedKbId(kb.id)}
                  className={`p-3.5 rounded-lg border transition-all cursor-pointer ${
                    isSelected
                      ? "border-cyan bg-cyan/10 shadow-sm shadow-cyan/10"
                      : "border-border/80 bg-surface/40 hover:bg-surface hover:border-border"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-bold text-xs text-foreground">
                      {kb.name}
                    </span>
                    <span className="text-[10px] font-mono text-cyan bg-cyan/20 px-1.5 py-0.5 rounded">
                      {kb.documents_count} docs
                    </span>
                  </div>
                  <div className="text-[10px] font-mono text-muted-foreground mt-1">
                    Index: <span className="text-foreground">{kb.vector_index}</span>
                  </div>
                  <div className="text-[10px] font-mono text-muted-foreground/80 mt-0.5">
                    Model: {kb.embedding_model}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Semantic Probe & Markdown Chunk Previews */}
        <div className="lg:col-span-2 rounded-lg border border-border/80 bg-surface/40 p-5 space-y-4">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-b border-border/60 pb-3">
            <div>
              <h2 className="text-sm font-bold text-foreground font-mono">
                COLLECTION: {currentKb.name}
              </h2>
              <span className="text-[10px] font-mono text-muted-foreground">
                Embedding: {currentKb.embedding_model} • Hybrid Sparse Search:{" "}
                {currentKb.hybrid_search ? "Enabled" : "Disabled"}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-border/70 hover:border-cyan/40 bg-surface text-xs font-mono text-muted-foreground hover:text-foreground transition-colors"
              >
                <UploadCloud className="w-3.5 h-3.5 text-cyan" />
                <span>Ingest Document</span>
              </button>
            </div>
          </div>

          {/* Semantic Query Testing */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold">
              TEST SEMANTIC RETRIEVAL (COSINE SIMILARITY)
            </label>
            <div className="relative">
              <Search className="w-4 h-4 text-muted-foreground absolute left-3 top-2.5" />
              <input
                type="text"
                placeholder="Ask or query vector space (e.g. 'How does sandbox hibernation work?')..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-24 py-2 rounded border border-border bg-void text-xs font-sans text-foreground focus:outline-none focus:border-cyan"
              />
              <button
                type="button"
                className="absolute right-1.5 top-1.5 px-2.5 py-1 rounded bg-cyan/20 hover:bg-cyan/30 text-cyan text-[11px] font-mono font-semibold transition-colors"
              >
                QUERY RAG
              </button>
            </div>
          </div>

          {/* Retrieved Markdown Chunks Preview */}
          <div className="space-y-3 pt-2">
            <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold flex items-center justify-between">
              <span>RETRIEVED KNOWLEDGE CHUNKS ({searchResults.length})</span>
              <span className="text-cyan">HNSW INDEX ACTIVE</span>
            </div>

            {searchResults.map((result) => (
              <div
                key={result.id}
                className="p-4 rounded border border-border/70 bg-void/80 space-y-2"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FileText className="w-3.5 h-3.5 text-cyan" />
                    <span className="text-xs font-mono font-semibold text-foreground">
                      {result.title}
                    </span>
                  </div>
                  <span className="text-[10px] font-mono font-bold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                    SIMILARITY: {(result.score * 100).toFixed(1)}%
                  </span>
                </div>

                {/* Render Rich Markdown of the chunk! */}
                <div className="pt-1 text-foreground/90">
                  <MarkdownRenderer content={result.content} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
