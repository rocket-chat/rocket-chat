"use client";

import React, { useState } from "react";
import { McpServerConfig } from "../../../types/mission";
import {
  Server,
  Plus,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Trash2,
} from "lucide-react";

export default function McpSettingsPage() {
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
  const [expandedServerId, setExpandedServerId] = useState<string | null>("mcp-github");
  const [showAddForm, setShowAddForm] = useState(false);
  const [authType, setAuthType] = useState<string>("none");
  const [apiKey, setApiKey] = useState("");
  const [authHeaderName, setAuthHeaderName] = useState("Authorization");
  const [authHeaderPrefix, setAuthHeaderPrefix] = useState("Bearer");
  const [guidance, setGuidance] = useState("");
  const [oauthTokenUrl, setOauthTokenUrl] = useState("");
  const [oauthClientId, setOauthClientId] = useState("");
  const [oauthClientSecret, setOauthClientSecret] = useState("");
  const [customHeaders, setCustomHeaders] = useState<Array<{ name: string; value: string; is_secret: boolean }>>([]);
  const [newHeaderKey, setNewHeaderKey] = useState("");
  const [newHeaderVal, setNewHeaderVal] = useState("");
  const [newHeaderSecret, setNewHeaderSecret] = useState(false);

  const [newServer, setNewServer] = useState<Partial<McpServerConfig>>({
    name: "",
    transport: "http",
    endpoint_or_command: "",
  });

  const handleProbe = (id: string) => {
    setProbingId(id);
    setTimeout(() => {
      setMcpServers((prev) =>
        prev.map((s) =>
          s.id === id
            ? { ...s, status: "connected", last_probed: "Just now", tools_count: s.tools_count }
            : s
        )
      );
      setProbingId(null);
    }, 1200);
  };

  const handleAddCustomHeader = () => {
    if (!newHeaderKey || !newHeaderVal) return;
    setCustomHeaders([...customHeaders, { name: newHeaderKey, value: newHeaderVal, is_secret: newHeaderSecret }]);
    setNewHeaderKey("");
    setNewHeaderVal("");
    setNewHeaderSecret(false);
  };

  const handleAddServer = () => {
    if (!newServer.name || !newServer.endpoint_or_command) return;
    const item: McpServerConfig = {
      id: `mcp-${Date.now().toString(36)}`,
      name: newServer.name,
      transport: (newServer.transport as "stdio" | "http" | "sse") || "http",
      endpoint_or_command: newServer.endpoint_or_command,
      guidance: guidance,
      auth_type: authType,
      status: "connected",
      tools_count: 2,
      last_probed: "Just now",
      has_api_key: Boolean(apiKey),
      api_key_fingerprint: apiKey ? `${apiKey.slice(0, 3)}...${apiKey.slice(-4)}` : null,
      headers_preview: customHeaders.map((h) => ({
        name: h.name,
        value: h.is_secret ? "***" : h.value,
        is_secret: h.is_secret,
      })),
      has_oauth_secret: Boolean(oauthClientSecret),
      tools: [
        {
          name: "execute_query",
          description: "Execute query or remote RPC operation against service.",
          parameters_summary: "query: string, options?: object",
        },
        {
          name: "get_schema",
          description: "Retrieve endpoint schema and supported operations.",
          parameters_summary: "depth?: number",
        },
      ],
    };
    setMcpServers([...mcpServers, item]);
    setShowAddForm(false);
    setNewServer({ name: "", transport: "http", endpoint_or_command: "" });
    setGuidance("");
    setApiKey("");
    setAuthType("none");
    setCustomHeaders([]);
    handleProbe(item.id);
  };

  const handleDeleteServer = (id: string) => {
    setMcpServers(mcpServers.filter((s) => s.id !== id));
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold font-mono tracking-wide text-foreground flex items-center gap-2">
            <Server className="w-5 h-5 text-cyan" />
            MODEL CONTEXT PROTOCOL (MCP) REGISTRY
          </h1>
          <p className="text-xs font-mono text-muted-foreground mt-1">
            Connect HTTP, SSE, and STDIO tool providers. Configure write-only encrypted secrets, multi-header auth, and custom agent guidance.
          </p>
        </div>

        <button
          type="button"
          onClick={() => setShowAddForm(!showAddForm)}
          className="flex items-center gap-2 px-3 py-2 rounded border border-cyan/40 bg-cyan/10 hover:bg-cyan/20 text-cyan text-xs font-mono font-semibold transition-all shadow-sm shadow-cyan/5 cursor-pointer"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>{showAddForm ? "CANCEL" : "REGISTER MCP SERVER"}</span>
        </button>
      </div>

      {/* Add New Server Card */}
      {showAddForm && (
        <div className="p-5 rounded-lg border border-cyan/40 bg-surface/80 space-y-4 shadow-xl">
          <div className="flex items-center justify-between border-b border-border/60 pb-2">
            <div className="text-xs font-mono font-bold text-cyan uppercase tracking-wider">
              REGISTER NEW MCP PROVIDER
            </div>
            <span className="text-[10px] font-mono text-muted-foreground bg-overlay px-2 py-0.5 rounded border border-border">
              🔒 WRITE-ONLY ENCRYPTED CREDENTIALS
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1 font-semibold">
                Server Display Name
              </label>
              <input
                type="text"
                placeholder="e.g. Stripe Billing MCP"
                value={newServer.name}
                onChange={(e) => setNewServer({ ...newServer, name: e.target.value })}
                className="w-full px-3 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
              />
            </div>
            <div>
              <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1 font-semibold">
                Transport Scheme
              </label>
              <select
                value={newServer.transport}
                onChange={(e) => setNewServer({ ...newServer, transport: e.target.value as "stdio" | "http" | "sse" })}
                className="w-full px-3 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
              >
                <option value="http">http (Standard HTTP REST/JSON-RPC)</option>
                <option value="sse">sse (HTTP Server-Sent Events stream)</option>
                <option value="stdio">stdio (Container Subprocess CLI)</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1 font-semibold">
                Endpoint URL / Command
              </label>
              <input
                type="text"
                placeholder={newServer.transport === "stdio" ? "e.g. npx -y @mcp/server-sqlite" : "https://api.internal.service/mcp"}
                value={newServer.endpoint_or_command}
                onChange={(e) => setNewServer({ ...newServer, endpoint_or_command: e.target.value })}
                className="w-full px-3 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
              />
            </div>
          </div>

          {/* Custom Guidance Context for Agent */}
          <div>
            <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1 font-semibold">
              Agent Tool Guidance & Usage Instructions (Custom Context)
            </label>
            <textarea
              rows={2}
              placeholder="e.g. Always check the customer currency before creating charges. Never run destructive charge refunds without confirmation."
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
              className="w-full px-3 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
            />
            <p className="text-[10px] font-mono text-muted-foreground mt-0.5">
              These guidelines are dynamically injected into the autonomous agent&apos;s system prompt to guide its reasoning.
            </p>
          </div>

          {/* Authentication Mechanism Selector */}
          <div className="pt-2 border-t border-border/50 space-y-3">
            <div className="flex items-center gap-3">
              <label className="text-[10px] font-mono uppercase text-muted-foreground font-semibold">
                Authentication Mode:
              </label>
              <select
                value={authType}
                onChange={(e) => setAuthType(e.target.value)}
                className="px-2 py-1 rounded border border-border bg-void text-xs font-mono text-cyan focus:outline-none focus:border-cyan"
              >
                <option value="none">No Authentication (Public / Local)</option>
                <option value="api_key">API Key / Bearer Token</option>
                <option value="custom_headers">Custom Headers (Multi-Secret)</option>
                <option value="oauth2">OAuth 2.0 (Client Credentials)</option>
              </select>
            </div>

            {authType === "api_key" && (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 rounded bg-void/60 border border-border/60">
                <div>
                  <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">Header Name</label>
                  <input
                    type="text"
                    value={authHeaderName}
                    onChange={(e) => setAuthHeaderName(e.target.value)}
                    className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">Prefix</label>
                  <input
                    type="text"
                    value={authHeaderPrefix}
                    onChange={(e) => setAuthHeaderPrefix(e.target.value)}
                    className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">Secret Key / Token (Write-Only)</label>
                  <input
                    type="password"
                    placeholder="sk_live_..."
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                </div>
              </div>
            )}

            {authType === "custom_headers" && (
              <div className="space-y-2 p-3 rounded bg-void/60 border border-border/60">
                <div className="text-[10px] font-mono text-muted-foreground uppercase font-semibold">Configure Headers</div>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    placeholder="Header Key (e.g. X-Org-ID)"
                    value={newHeaderKey}
                    onChange={(e) => setNewHeaderKey(e.target.value)}
                    className="flex-1 px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                  <input
                    type={newHeaderSecret ? "password" : "text"}
                    placeholder="Header Value"
                    value={newHeaderVal}
                    onChange={(e) => setNewHeaderVal(e.target.value)}
                    className="flex-1 px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                  <label className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={newHeaderSecret}
                      onChange={(e) => setNewHeaderSecret(e.target.checked)}
                      className="rounded border-border"
                    />
                    <span>Secret</span>
                  </label>
                  <button
                    type="button"
                    onClick={handleAddCustomHeader}
                    className="px-2.5 py-1 rounded bg-overlay border border-border hover:bg-surface text-cyan text-xs font-mono cursor-pointer"
                  >
                    Add Header
                  </button>
                </div>
                {customHeaders.length > 0 && (
                  <div className="flex flex-wrap gap-2 pt-2">
                    {customHeaders.map((h, i) => (
                      <span key={i} className="text-[10px] font-mono px-2 py-0.5 rounded bg-surface border border-border text-foreground">
                        {h.name}: {h.is_secret ? "••••••••" : h.value}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {authType === "oauth2" && (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 rounded bg-void/60 border border-border/60">
                <div>
                  <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">Token URL</label>
                  <input
                    type="text"
                    placeholder="https://oauth.provider.com/token"
                    value={oauthTokenUrl}
                    onChange={(e) => setOauthTokenUrl(e.target.value)}
                    className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">Client ID</label>
                  <input
                    type="text"
                    value={oauthClientId}
                    onChange={(e) => setOauthClientId(e.target.value)}
                    className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">Client Secret (Write-Only)</label>
                  <input
                    type="password"
                    value={oauthClientSecret}
                    onChange={(e) => setOauthClientSecret(e.target.value)}
                    className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                  />
                </div>
              </div>
            )}
          </div>

          <div className="flex justify-end pt-2 border-t border-border/40">
            <button
              type="button"
              onClick={handleAddServer}
              className="px-4 py-1.5 rounded bg-cyan hover:bg-cyan/90 text-void font-bold text-xs font-mono transition-colors cursor-pointer"
            >
              PROBE & CONNECT SERVER
            </button>
          </div>
        </div>
      )}

      {/* Server Groups */}
      <div className="space-y-6">
        {/* Built-in Providers */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-xs font-mono font-semibold text-muted-foreground uppercase tracking-wider">
            <span className="w-2 h-2 rounded-full bg-cyan inline-block" />
            <span>Built-in Platform Integrations</span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-cyan/10 border border-cyan/30 text-cyan">
              Core Runtime
            </span>
          </div>

          <div className="space-y-3">
            {mcpServers
              .filter((s) => s.is_builtin)
              .map((server) => {
                const isExpanded = expandedServerId === server.id;
                const isProbing = probingId === server.id;

                return (
                  <div
                    key={server.id}
                    className="rounded-lg border border-border/80 bg-surface/40 overflow-hidden transition-all"
                  >
                    <div className="p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <button
                          type="button"
                          onClick={() => setExpandedServerId(isExpanded ? null : server.id)}
                          className="p-1 text-muted-foreground hover:text-foreground cursor-pointer"
                          aria-label={isExpanded ? "Collapse tool list" : "Expand tool list"}
                        >
                          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                        </button>
                        <div className="w-8 h-8 rounded border border-border/70 bg-void flex items-center justify-center">
                          <Server className="w-4 h-4 text-cyan" />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-mono font-bold text-xs text-foreground">
                              {server.name}
                            </span>
                            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-cyan/15 border border-cyan/30 text-cyan uppercase font-semibold">
                              Built-in
                            </span>
                            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-overlay border border-border text-muted-foreground uppercase">
                              {server.transport}
                            </span>
                          </div>
                          {server.description && (
                            <div className="text-[11px] text-muted-foreground font-sans mt-0.5">
                              {server.description}
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-3 self-end sm:self-auto text-xs font-mono">
                        <div className="flex items-center gap-1.5">
                          {server.status === "connected" ? (
                            <span className="flex items-center gap-1 text-emerald-400 text-[11px]">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              <span>READY ({server.tools_count} TOOLS)</span>
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 text-amber-400 text-[11px]">
                              <AlertCircle className="w-3.5 h-3.5" />
                              <span>PROBING...</span>
                            </span>
                          )}
                        </div>

                        <button
                          type="button"
                          onClick={() => handleProbe(server.id)}
                          disabled={isProbing}
                          className="flex items-center gap-1 px-2.5 py-1 rounded border border-border/80 hover:border-cyan/40 bg-surface/50 text-[11px] text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50 cursor-pointer"
                        >
                          <RefreshCw className={`w-3 h-3 ${isProbing ? "animate-spin text-cyan" : ""}`} />
                          <span>{isProbing ? "PROBING" : "VERIFY"}</span>
                        </button>
                      </div>
                    </div>

                    {/* Discovered tools list accordion */}
                    {isExpanded && (
                      <div className="border-t border-border/60 bg-void/60 p-4 space-y-2 text-xs font-mono">
                        <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase font-semibold">
                          <span>DISCOVERED CAPABILITIES & TOOL SCHEMAS</span>
                          <span>LAST SYNC: {server.last_probed}</span>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                          {(server.tools && server.tools.length > 0) ? (
                            server.tools.map((t) => (
                              <div key={t.name} className="p-2.5 rounded border border-border/60 bg-surface/30">
                                <div className="font-semibold text-cyan text-xs">{t.name}</div>
                                <div className="text-[10px] text-muted-foreground mt-0.5">
                                  {t.description}
                                </div>
                                {t.parameters_summary && (
                                  <code className="text-[10px] text-muted-foreground/80 mt-1 block">
                                    args: &#123; {t.parameters_summary} &#125;
                                  </code>
                                )}
                              </div>
                            ))
                          ) : (
                            <div className="p-2.5 rounded border border-border/60 bg-surface/30 text-muted-foreground text-xs col-span-2">
                              No tools loaded for this provider yet.
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
          </div>
        </div>

        {/* Custom External MCP Providers */}
        {mcpServers.some((s) => !s.is_builtin) && (
          <div className="space-y-3 pt-2">
            <div className="flex items-center gap-2 text-xs font-mono font-semibold text-muted-foreground uppercase tracking-wider">
              <span className="w-2 h-2 rounded-full bg-border inline-block" />
              <span>Custom External Providers</span>
            </div>

            <div className="space-y-3">
              {mcpServers
                .filter((s) => !s.is_builtin)
                .map((server) => {
                  const isExpanded = expandedServerId === server.id;
                  const isProbing = probingId === server.id;

                  return (
                    <div
                      key={server.id}
                      className="rounded-lg border border-border/80 bg-surface/40 overflow-hidden transition-all"
                    >
                      <div className="p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                        <div className="flex items-center gap-3">
                          <button
                            type="button"
                            onClick={() => setExpandedServerId(isExpanded ? null : server.id)}
                            className="p-1 text-muted-foreground hover:text-foreground cursor-pointer"
                            aria-label={isExpanded ? "Collapse tool list" : "Expand tool list"}
                          >
                            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                          </button>
                          <div className="w-8 h-8 rounded border border-border/70 bg-void flex items-center justify-center">
                            <Server className="w-4 h-4 text-muted-foreground" />
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="font-mono font-bold text-xs text-foreground">
                                {server.name}
                              </span>
                              <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-overlay border border-border text-muted-foreground uppercase">
                                {server.transport}
                              </span>
                            </div>
                            <div className="text-[11px] font-mono text-muted-foreground truncate max-w-md mt-0.5">
                              {server.endpoint_or_command}
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center gap-3 self-end sm:self-auto text-xs font-mono">
                          <div className="flex items-center gap-1.5">
                            {server.status === "connected" ? (
                              <span className="flex items-center gap-1 text-emerald-400 text-[11px]">
                                <CheckCircle2 className="w-3.5 h-3.5" />
                                <span>CONNECTED ({server.tools_count} TOOLS)</span>
                              </span>
                            ) : (
                              <span className="flex items-center gap-1 text-amber-400 text-[11px]">
                                <AlertCircle className="w-3.5 h-3.5" />
                                <span>PROBING...</span>
                              </span>
                            )}
                          </div>

                          <button
                            type="button"
                            onClick={() => handleProbe(server.id)}
                            disabled={isProbing}
                            className="flex items-center gap-1 px-2.5 py-1 rounded border border-border/80 hover:border-cyan/40 bg-surface/50 text-[11px] text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50 cursor-pointer"
                          >
                            <RefreshCw className={`w-3 h-3 ${isProbing ? "animate-spin text-cyan" : ""}`} />
                            <span>{isProbing ? "PROBING" : "LIVE PROBE"}</span>
                          </button>

                          <button
                            type="button"
                            onClick={() => handleDeleteServer(server.id)}
                            className="p-1 text-muted-foreground hover:text-rose-400 transition-colors cursor-pointer"
                            title="Remove server"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      {/* Discovered tools list accordion */}
                      {isExpanded && (
                        <div className="border-t border-border/60 bg-void/60 p-4 space-y-2 text-xs font-mono">
                          <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase font-semibold">
                            <span>DISCOVERED CAPABILITIES & TOOL SCHEMAS</span>
                            <span>LAST SYNC: {server.last_probed}</span>
                          </div>

                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                            {(server.tools && server.tools.length > 0) ? (
                              server.tools.map((t) => (
                                <div key={t.name} className="p-2.5 rounded border border-border/60 bg-surface/30">
                                  <div className="font-semibold text-cyan text-xs">{t.name}</div>
                                  <div className="text-[10px] text-muted-foreground mt-0.5">
                                    {t.description}
                                  </div>
                                  {t.parameters_summary && (
                                    <code className="text-[10px] text-muted-foreground/80 mt-1 block">
                                      args: &#123; {t.parameters_summary} &#125;
                                    </code>
                                  )}
                                </div>
                              ))
                            ) : (
                              <div className="p-2.5 rounded border border-border/60 bg-surface/30 text-muted-foreground text-xs col-span-2">
                                No schemas discovered. Run live probe to introspect tools.
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
