"use client";

import React, { useState, useEffect } from "react";
import { useMissionStore } from "../../lib/store";
import {
  X,
  Server,
  Code2,
  Database,
  Bot,
  Shield,
  Plus,
  RefreshCw,
  CheckCircle2,
  Play,
  Key,
  Layers,
} from "lucide-react";
import { McpServerConfig, CustomSkillConfig, KnowledgeBaseConfig, AgentPersonaConfig } from "../../types/mission";

type SettingsTab = "mcp" | "skills" | "knowledge" | "agents" | "org";

export const SettingsModal: React.FC = () => {
  const { isSettingsOpen, setIsSettingsOpen, availableModels } = useMissionStore();
  const [activeTab, setActiveTab] = useState<SettingsTab>("mcp");

  // Keyboard dismiss (Escape)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsSettingsOpen(false);
      }
    };
    if (isSettingsOpen) {
      window.addEventListener("keydown", handleKeyDown);
    }
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSettingsOpen, setIsSettingsOpen]);

  // Sample initial states for Enterprise Configuration
  const [mcpServers, setMcpServers] = useState<McpServerConfig[]>([
    {
      id: "mcp-filesystem",
      name: "Workspace Filesystem",
      description: "Direct workspace file inspection, surgical edits, directory traversal, and patch operations.",
      transport: "stdio",
      endpoint_or_command: "Built-in Sandbox Runtime (/workspace)",
      status: "connected",
      tools_count: 4,
      last_probed: "Just now",
      is_builtin: true,
      tools: [
        {
          name: "file_read",
          description: "Read file contents with syntax highlighting and line range slicing.",
          parameters_summary: "path: string, start_line?: number, end_line?: number",
        },
        {
          name: "file_write",
          description: "Atomic file creation or full overwrite in workspace.",
          parameters_summary: "path: string, content: string, overwrite?: boolean",
        },
        {
          name: "file_edit",
          description: "Perform resilient block replacement in a workspace file.",
          parameters_summary: "path: string, target_block: string, replacement_block: string",
        },
        {
          name: "apply_patch",
          description: "Apply standard unified diff patch to a workspace file.",
          parameters_summary: "path: string, patch_content: string",
        },
      ],
    },
    {
      id: "mcp-github",
      name: "GitHub Integration",
      description: "Repository navigation, PR reviews, issue management, and git commit history.",
      transport: "stdio",
      endpoint_or_command: "npx -y @modelcontextprotocol/server-github",
      status: "connected",
      tools_count: 3,
      last_probed: "Just now",
      is_builtin: true,
      tools: [
        {
          name: "list_repositories",
          description: "List repositories accessible to the authenticated installation.",
          parameters_summary: "page?: number, per_page?: number",
        },
        {
          name: "create_issue",
          description: "Create an issue in a designated target repository.",
          parameters_summary: "title: string, body?: string, labels?: string[]",
        },
        {
          name: "search_repositories",
          description: "Search repositories matching query keywords or topics.",
          parameters_summary: "query: string, sort?: string",
        },
      ],
    },
    {
      id: "mcp-postgres",
      name: "Database Inspector",
      description: "Query PostgreSQL schemas, execute read-only SQL, and inspect table definitions.",
      transport: "stdio",
      endpoint_or_command: "uvx mcp-server-postgres --dsn postgresql://...",
      status: "connected",
      tools_count: 2,
      last_probed: "2 mins ago",
      is_builtin: true,
      tools: [
        {
          name: "query_database",
          description: "Run analytical read-only SQL queries against PostgreSQL tables.",
          parameters_summary: "sql: string, limit?: number",
        },
        {
          name: "describe_schema",
          description: "Inspect table columns, primary keys, and foreign relationships.",
          parameters_summary: "table_name: string",
        },
      ],
    },
  ]);

  const [probingId, setProbingId] = useState<string | null>(null);

  const [skills] = useState<CustomSkillConfig[]>([
    {
      id: "skill-ast-parser",
      name: "ast_symbol_analyzer",
      description: "Extracts classes, function signatures, and imports using Python AST safely",
      runtime: "python",
      is_active: true,
      signature: "def ast_symbol_analyzer(file_path: str) -> dict",
      code: `import ast\n\ndef ast_symbol_analyzer(file_path: str) -> dict:\n    with open(file_path, "r", encoding="utf-8") as f:\n        tree = ast.parse(f.read())\n    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]\n    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]\n    return {"classes": classes, "functions": funcs}`,
    },
    {
      id: "skill-json-validator",
      name: "schema_validator",
      description: "Validates JSON against Draft-07 schemas inside sandbox runtime",
      runtime: "python",
      is_active: true,
      signature: "def schema_validator(data: dict, schema: dict) -> bool",
      code: `import jsonschema\n\ndef schema_validator(data: dict, schema: dict) -> bool:\n    try:\n        jsonschema.validate(instance=data, schema=schema)\n        return True\n    except jsonschema.ValidationError:\n        return False`,
    },
  ]);

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
      id: "kb-api-contracts",
      name: "OpenAPI Specifications & Schemas",
      vector_index: "pgvector_hnsw_api_contracts",
      documents_count: 18,
      embedding_model: "text-embedding-3-small (1536d)",
      hybrid_search: true,
    },
  ]);

  const [personas, setPersonas] = useState<AgentPersonaConfig[]>([
    {
      id: "persona-general",
      name: "General Software Engineer",
      role_title: "Senior Autonomous Engineer",
      description:
        "Autonomous full-stack engineering agent with full access to terminal execution, file inspection and editing, web lookup, git operations, and interactive decision gates.",
      system_prompt:
        "You are a Senior Autonomous Software Engineer. You write clean, modular, production-grade code adhering to strict types and software craftsmanship. Plan methodically, inspect files, apply surgical diffs, and verify your work with automated tests.",
      model: "openrouter/deepseek/deepseek-v4.1-flash",
      whitelisted_tools: [
        "bash_exec",
        "file_read",
        "file_write",
        "file_edit",
        "apply_patch",
        "web_fetch",
        "web_search",
        "ask_question",
        "session_rename",
        "update_task_checklist",
      ],
      icon: "bot",
    },
  ]);

  const [activePersonaId, setActivePersonaId] = useState<string>("persona-general");
  const [activeSkillId, setActiveSkillId] = useState<string>("skill-ast-parser");

  const handleProbeMcp = (id: string) => {
    setProbingId(id);
    setTimeout(() => {
      setMcpServers((prev) =>
        prev.map((s) =>
          s.id === id ? { ...s, status: "connected", last_probed: "Just now" } : s
        )
      );
      setProbingId(null);
    }, 1200);
  };

  if (!isSettingsOpen) return null;

  const currentPersona = personas.find((p) => p.id === activePersonaId) || personas[0];
  const currentSkill = skills.find((s) => s.id === activeSkillId) || skills[0];

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 select-none">
      <div className="w-full max-w-5xl h-[85vh] rounded-xl border border-border bg-surface shadow-2xl flex flex-col overflow-hidden text-foreground">
        {/* Modal Top Header */}
        <div className="h-14 px-6 border-b border-border bg-elevated/70 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded bg-cyan/15 border border-cyan/30 flex items-center justify-center">
              <Layers className="w-4 h-4 text-cyan" />
            </div>
            <div>
              <h2 className="text-sm font-bold tracking-wide font-display">
                ENTERPRISE CONTROL & GOVERNANCE
              </h2>
              <p className="text-[11px] text-muted-foreground font-mono">
                Model Context Protocol, Isolated Sandboxed Tools, PGVector RAG, and Personas
              </p>
            </div>
          </div>

          <button
            onClick={() => setIsSettingsOpen(false)}
            className="p-1.5 rounded hover:bg-overlay text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body: Left Nav + Right Content */}
        <div className="flex-1 flex overflow-hidden">
          {/* Navigation Sidebar */}
          <div className="w-56 border-r border-border bg-void/40 p-3 space-y-1 font-mono text-xs">
            <button
              onClick={() => setActiveTab("mcp")}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded font-medium transition-colors cursor-pointer ${
                activeTab === "mcp"
                  ? "bg-cyan/15 text-cyan border border-cyan/30"
                  : "text-muted-foreground hover:bg-elevated hover:text-foreground"
              }`}
            >
              <Server className="w-3.5 h-3.5" />
              <span>MCP Servers</span>
            </button>

            <button
              onClick={() => setActiveTab("skills")}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded font-medium transition-colors cursor-pointer ${
                activeTab === "skills"
                  ? "bg-cyan/15 text-cyan border border-cyan/30"
                  : "text-muted-foreground hover:bg-elevated hover:text-foreground"
              }`}
            >
              <Code2 className="w-3.5 h-3.5" />
              <span>Custom Skills & Tools</span>
            </button>

            <button
              onClick={() => setActiveTab("knowledge")}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded font-medium transition-colors cursor-pointer ${
                activeTab === "knowledge"
                  ? "bg-cyan/15 text-cyan border border-cyan/30"
                  : "text-muted-foreground hover:bg-elevated hover:text-foreground"
              }`}
            >
              <Database className="w-3.5 h-3.5" />
              <span>Knowledge Bases</span>
            </button>

            <button
              onClick={() => setActiveTab("agents")}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded font-medium transition-colors cursor-pointer ${
                activeTab === "agents"
                  ? "bg-cyan/15 text-cyan border border-cyan/30"
                  : "text-muted-foreground hover:bg-elevated hover:text-foreground"
              }`}
            >
              <Bot className="w-3.5 h-3.5" />
              <span>Custom Agents</span>
            </button>

            <div className="pt-2 my-2 border-t border-border/60" />

            <button
              onClick={() => setActiveTab("org")}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded font-medium transition-colors cursor-pointer ${
                activeTab === "org"
                  ? "bg-cyan/15 text-cyan border border-cyan/30"
                  : "text-muted-foreground hover:bg-elevated hover:text-foreground"
              }`}
            >
              <Shield className="w-3.5 h-3.5" />
              <span>Org & BYOK Keys</span>
            </button>
          </div>

          {/* Right Pane Content */}
          <div className="flex-1 p-6 overflow-y-auto bg-void/20">
            {/* TAB 1: MCP REGISTRY */}
            {activeTab === "mcp" && (
              <div className="space-y-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-bold font-display">Model Context Protocol (MCP) Registry</h3>
                    <p className="text-xs text-muted-foreground font-sans mt-0.5">
                      State-of-the-art MCP architecture: schemas cached in database, probed on configuration, background revalidation every 30m.
                    </p>
                  </div>
                  <button className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-flame hover:bg-flame-hover text-white text-xs font-mono font-bold transition-colors cursor-pointer shadow-md">
                    <Plus className="w-3.5 h-3.5" />
                    <span>Add Server</span>
                  </button>
                </div>

                <div className="space-y-4">
                  {/* Built-in Providers */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-xs font-mono font-semibold text-muted-foreground uppercase tracking-wider">
                      <span className="w-2 h-2 rounded-full bg-cyan inline-block" />
                      <span>Built-in Platform Integrations</span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-cyan/10 border border-cyan/30 text-cyan">
                        Core Runtime
                      </span>
                    </div>

                    <div className="grid gap-2.5">
                      {mcpServers.map((server) => (
                        <div
                          key={server.id}
                          className="p-3.5 rounded-lg border border-border bg-elevated/40 flex flex-col gap-2.5"
                        >
                          <div className="flex items-center justify-between gap-4">
                            <div className="space-y-1">
                              <div className="flex items-center gap-2">
                                <span className="font-bold text-sm text-foreground">{server.name}</span>
                                <span className="text-[10px] px-2 py-0.5 rounded bg-cyan/15 border border-cyan/30 font-mono text-cyan uppercase font-semibold">
                                  Built-in
                                </span>
                                <span className="text-[10px] px-2 py-0.5 rounded bg-diff-add/10 border border-diff-add/30 text-diff-add font-mono flex items-center gap-1">
                                  <CheckCircle2 className="w-2.5 h-2.5" />
                                  {server.tools_count} Tools Ready
                                </span>
                              </div>
                              {server.description && (
                                <div className="text-xs text-muted-foreground font-sans">
                                  {server.description}
                                </div>
                              )}
                            </div>

                            <button
                              onClick={() => handleProbeMcp(server.id)}
                              disabled={probingId === server.id}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-overlay hover:bg-surface border border-border text-xs font-mono text-cyan hover:border-cyan/40 transition-colors cursor-pointer shrink-0"
                            >
                              <RefreshCw className={`w-3.5 h-3.5 ${probingId === server.id ? "animate-spin" : ""}`} />
                              <span>{probingId === server.id ? "Probing..." : "Verify"}</span>
                            </button>
                          </div>

                          {/* Tool Schemas */}
                          {server.tools && server.tools.length > 0 && (
                            <div className="pt-2 border-t border-border/50">
                              <div className="text-[10px] font-mono font-semibold uppercase text-muted-foreground mb-1.5">
                                Discovered Capabilities & Tool Schemas:
                              </div>
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                {server.tools.map((t) => (
                                  <div
                                    key={t.name}
                                    className="p-2 rounded bg-void/50 border border-border/40 text-[11px] font-mono"
                                  >
                                    <div className="font-bold text-cyan">{t.name}</div>
                                    <div className="text-[10px] text-muted-foreground font-sans mt-0.5">
                                      {t.description}
                                    </div>
                                    {t.parameters_summary && (
                                      <code className="text-[9px] text-muted-foreground/80 mt-1 block">
                                        args: &#123; {t.parameters_summary} &#125;
                                      </code>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* TAB 2: CUSTOM SKILLS & PYTHON TOOLS */}
            {activeTab === "skills" && (
              <div className="space-y-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-bold font-display">Custom Skills & Sandboxed Python Tools</h3>
                    <p className="text-xs text-muted-foreground font-sans mt-0.5">
                      Secure runtime: code executed exclusively inside isolated sandbox containers. AST signature validation enforced.
                    </p>
                  </div>
                  <button className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-flame hover:bg-flame-hover text-white text-xs font-mono font-bold transition-colors cursor-pointer shadow-md">
                    <Plus className="w-3.5 h-3.5" />
                    <span>New Python Tool</span>
                  </button>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  {/* Skill List */}
                  <div className="col-span-1 space-y-2">
                    {skills.map((s) => (
                      <div
                        key={s.id}
                        onClick={() => setActiveSkillId(s.id)}
                        className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                          s.id === currentSkill.id
                            ? "border-cyan/50 bg-cyan/10"
                            : "border-border bg-elevated/40 hover:bg-elevated"
                        }`}
                      >
                        <div className="font-bold text-xs text-foreground font-mono">{s.name}</div>
                        <div className="text-[11px] text-muted-foreground line-clamp-2 mt-1">
                          {s.description}
                        </div>
                        <div className="flex items-center gap-2 mt-2 text-[10px] font-mono text-cyan">
                          <span>{s.runtime.toUpperCase()} RUNTIME</span>
                          <span className="text-status-live">● ACTIVE</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Skill Editor View */}
                  <div className="col-span-2 rounded-lg border border-border bg-surface p-4 flex flex-col gap-3 font-mono text-xs">
                    <div className="flex items-center justify-between pb-2 border-b border-border">
                      <div className="font-bold text-sm text-foreground">{currentSkill.name}</div>
                      <div className="flex items-center gap-1 px-2 py-0.5 rounded bg-diff-add/10 border border-diff-add/30 text-diff-add text-[11px]">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>AST Validated & Sandboxed</span>
                      </div>
                    </div>

                    <div className="text-muted-foreground text-[11px] font-sans">
                      {currentSkill.description}
                    </div>

                    <div className="p-2 rounded bg-void border border-border text-cyan/90 text-[11px]">
                      {currentSkill.signature}
                    </div>

                    <div className="flex-1 rounded bg-void border border-border p-3 overflow-x-auto text-[11px] text-foreground/90 whitespace-pre leading-relaxed">
                      {currentSkill.code}
                    </div>

                    <div className="flex justify-end gap-2 pt-2">
                      <button className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-overlay hover:bg-elevated border border-border text-xs text-foreground transition-colors cursor-pointer">
                        <Play className="w-3.5 h-3.5 text-cyan" />
                        <span>Dry-Run in Sandbox</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* TAB 3: KNOWLEDGE BASE ENGINE */}
            {activeTab === "knowledge" && (
              <div className="space-y-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-bold font-display">Vector Knowledge Base Engine</h3>
                    <p className="text-xs text-muted-foreground font-sans mt-0.5">
                      PostgreSQL pgvector with HNSW indexing and hybrid BM25/trigram reranking for ultra-low latency semantic retrieval.
                    </p>
                  </div>
                  <button className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-flame hover:bg-flame-hover text-white text-xs font-mono font-bold transition-colors cursor-pointer shadow-md">
                    <Plus className="w-3.5 h-3.5" />
                    <span>Create Collection</span>
                  </button>
                </div>

                <div className="grid gap-3">
                  {knowledgeBases.map((kb) => (
                    <div
                      key={kb.id}
                      className="p-4 rounded-lg border border-border bg-elevated/50 flex items-center justify-between"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-sm text-foreground">{kb.name}</span>
                          <span className="text-[10px] px-2 py-0.5 rounded bg-overlay border border-border font-mono text-muted-foreground">
                            {kb.vector_index}
                          </span>
                          <span className="text-[10px] px-2 py-0.5 rounded bg-cyan/10 border border-cyan/30 text-cyan font-mono">
                            Hybrid Search Enabled
                          </span>
                        </div>
                        <div className="text-xs text-muted-foreground font-mono">
                          Embedding Model: {kb.embedding_model} • {kb.documents_count} Document Chunks
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <button className="px-3 py-1.5 rounded bg-overlay hover:bg-elevated border border-border text-xs font-mono text-foreground hover:border-cyan/40 transition-colors cursor-pointer">
                          Query Chunks
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* TAB 4: CUSTOM AGENT PERSONAS */}
            {activeTab === "agents" && (
              <div className="space-y-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-bold font-display">Agent Persona Architecture</h3>
                    <p className="text-xs text-muted-foreground font-sans mt-0.5">
                      Configure purpose-built autonomous agents with tailored system prompts, inference models, and scoped tool whitelists.
                    </p>
                  </div>
                  <button className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-flame hover:bg-flame-hover text-white text-xs font-mono font-bold transition-colors cursor-pointer shadow-md">
                    <Plus className="w-3.5 h-3.5" />
                    <span>New Persona</span>
                  </button>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  {/* Persona List */}
                  <div className="col-span-1 space-y-2">
                    {personas.map((p) => (
                      <div
                        key={p.id}
                        onClick={() => setActivePersonaId(p.id)}
                        className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                          p.id === currentPersona.id
                            ? "border-cyan/50 bg-cyan/10"
                            : "border-border bg-elevated/40 hover:bg-elevated"
                        }`}
                      >
                        <div className="font-bold text-xs text-foreground">{p.name}</div>
                        <div className="text-[11px] text-muted-foreground font-mono mt-0.5">
                          {p.role_title}
                        </div>
                        <div className="text-[10px] text-cyan font-mono mt-2">
                          {p.whitelisted_tools.length} Tools Whitelisted
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Persona Detail Form */}
                  <div className="col-span-2 rounded-lg border border-border bg-surface p-4 flex flex-col gap-3 font-mono text-xs">
                    <div>
                      <label className="text-[11px] text-muted-foreground uppercase tracking-wide">
                        Persona Name & Title
                      </label>
                      <input
                        type="text"
                        value={currentPersona.name}
                        onChange={(e) => {
                          const val = e.target.value;
                          setPersonas((prev) =>
                            prev.map((p) => (p.id === currentPersona.id ? { ...p, name: val } : p))
                          );
                        }}
                        className="w-full mt-1 px-3 py-1.5 rounded border border-border bg-void text-foreground font-sans text-xs focus:outline-none focus:border-cyan"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-muted-foreground uppercase tracking-wide">
                        Default Model
                      </label>
                      <select
                        value={currentPersona.model}
                        onChange={(e) => {
                          const val = e.target.value;
                          setPersonas((prev) =>
                            prev.map((p) => (p.id === currentPersona.id ? { ...p, model: val } : p))
                          );
                        }}
                        className="w-full mt-1 px-3 py-1.5 rounded border border-border bg-void text-foreground font-mono text-xs focus:outline-none focus:border-cyan"
                      >
                        {availableModels.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.name} ({m.provider})
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="flex-1 flex flex-col">
                      <label className="text-[11px] text-muted-foreground uppercase tracking-wide mb-1">
                        System Prompt / Persona Context
                      </label>
                      <textarea
                        rows={4}
                        value={currentPersona.system_prompt}
                        onChange={(e) => {
                          const val = e.target.value;
                          setPersonas((prev) =>
                            prev.map((p) => (p.id === currentPersona.id ? { ...p, system_prompt: val } : p))
                          );
                        }}
                        className="w-full flex-1 p-2.5 rounded border border-border bg-void text-foreground/90 font-mono text-xs focus:outline-none focus:border-cyan resize-none leading-relaxed"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-muted-foreground uppercase tracking-wide mb-1 block">
                        Whitelisted Tools
                      </label>
                      <div className="flex flex-wrap gap-1.5">
                        {currentPersona.whitelisted_tools.map((t) => (
                          <span
                            key={t}
                            className="px-2 py-1 rounded bg-overlay border border-border text-[10px] text-foreground flex items-center gap-1"
                          >
                            <CheckCircle2 className="w-2.5 h-2.5 text-cyan" />
                            {t}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* TAB 5: ORG & BYOK */}
            {activeTab === "org" && (
              <div className="space-y-6">
                <div>
                  <h3 className="text-base font-bold font-display">Organization & BYOK (Bring Your Own Keys)</h3>
                  <p className="text-xs text-muted-foreground font-sans mt-0.5">
                    Tenant workspace isolation with end-to-end encrypted API key vaults.
                  </p>
                </div>

                {/* API Key Vault */}
                <div className="rounded-lg border border-border bg-elevated/50 p-4 space-y-4">
                  <div className="flex items-center gap-2 font-bold text-sm">
                    <Key className="w-4 h-4 text-cyan" />
                    <span>Inference Provider API Keys</span>
                  </div>

                  <div className="space-y-3 font-mono text-xs">
                    <div>
                      <label className="text-[11px] text-muted-foreground block mb-1">
                        OpenRouter API Key
                      </label>
                      <input
                        type="password"
                        defaultValue="sk-or-v1-********************************"
                        className="w-full px-3 py-1.5 rounded border border-border bg-void text-foreground text-xs focus:outline-none focus:border-cyan"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-muted-foreground block mb-1">
                        Anthropic API Key
                      </label>
                      <input
                        type="password"
                        placeholder="sk-ant-api03-..."
                        className="w-full px-3 py-1.5 rounded border border-border bg-void text-foreground text-xs focus:outline-none focus:border-cyan"
                      />
                    </div>

                    <div>
                      <label className="text-[11px] text-muted-foreground block mb-1">
                        OpenAI API Key
                      </label>
                      <input
                        type="password"
                        placeholder="sk-proj-..."
                        className="w-full px-3 py-1.5 rounded border border-border bg-void text-foreground text-xs focus:outline-none focus:border-cyan"
                      />
                    </div>
                  </div>

                  <div className="flex justify-end pt-2">
                    <button className="px-4 py-1.5 rounded bg-flame hover:bg-flame-hover text-white font-mono text-xs font-bold transition-colors cursor-pointer">
                      Save Key Vault
                    </button>
                  </div>
                </div>

                {/* Team Members */}
                <div className="rounded-lg border border-border bg-elevated/50 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-sm">Active Organization Roster</span>
                    <span className="text-[11px] text-muted-foreground font-mono">Managed Identity</span>
                  </div>

                  <div className="space-y-2 font-mono text-xs">
                    <div className="flex items-center justify-between p-2 rounded bg-void border border-border">
                      <div className="flex items-center gap-2">
                        <span className="w-6 h-6 rounded-full bg-cyan/20 text-cyan flex items-center justify-center font-bold text-[10px]">
                          OP
                        </span>
                        <span>Operator (Current Session)</span>
                      </div>
                      <span className="px-2 py-0.5 rounded bg-flame/15 border border-flame/30 text-flame text-[10px]">
                        AUTHENTICATED
                      </span>
                    </div>

                    <div className="flex items-center justify-between p-2 rounded bg-void border border-border">
                      <div className="flex items-center gap-2">
                        <span className="w-6 h-6 rounded-full bg-overlay text-muted-foreground flex items-center justify-center font-bold text-[10px]">
                          AG
                        </span>
                        <span>agent-service-account</span>
                      </div>
                      <span className="px-2 py-0.5 rounded bg-cyan/15 border border-cyan/30 text-cyan text-[10px]">
                        AI SERVICE WORKER
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
