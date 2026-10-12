"use client";

import React, { useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Plus,
  RefreshCw,
} from "lucide-react";
import { Field, Input, Select, Textarea } from "../../ui/Field";
import { Disclosure } from "../../ui/Disclosure";
import { Modal } from "../../ui/Modal";
import { McpServerConfig, McpToolSchema } from "../../../types/mission";

interface McpServerFormProps {
  scope: "user" | "org";
  isAdmin: boolean;
  onSaved: (server: McpServerConfig, message: string) => void;
  onCancel: () => void;
}

/** Registers a new MCP server: transport, endpoint, credentials, a connection test. */
export const McpServerForm: React.FC<McpServerFormProps> = ({
  scope: formScope,
  isAdmin,
  onSaved,
  onCancel,
}) => {
  const [validating, setValidating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<{
    valid: boolean;
    message?: string;
    error?: string | null;
    tools_count?: number;
    tools?: McpToolSchema[];
  } | null>(null);

  // Server Form Fields
  const [serverName, setServerName] = useState("");
  const [transport, setTransport] = useState<"http" | "sse" | "streamable_http" | "websocket" | "stdio">("http");
  const [endpoint, setEndpoint] = useState("");
  const [guidance, setGuidance] = useState("");
  const [authType, setAuthType] = useState<string>("none");
  const [apiKey, setApiKey] = useState("");
  const [authHeaderName, setAuthHeaderName] = useState("Authorization");
  const [authHeaderPrefix, setAuthHeaderPrefix] = useState("Bearer");
  const [oauthAuthUrl, setOauthAuthUrl] = useState("");
  const [oauthTokenUrl, setOauthTokenUrl] = useState("");
  const [oauthClientId, setOauthClientId] = useState("");
  const [oauthClientSecret, setOauthClientSecret] = useState("");
  const [oauthScopes, setOauthScopes] = useState("");
  const [oauthMode, setOauthMode] = useState<"org" | "member">("org");
  const [customHeaders, setCustomHeaders] = useState<Array<{ name: string; value: string; is_secret: boolean }>>([]);
  const [newHeaderKey, setNewHeaderKey] = useState("");
  const [newHeaderVal, setNewHeaderVal] = useState("");
  const [newHeaderSecret, setNewHeaderSecret] = useState(false);

  // The API takes credentials nested under `auth`; this is the single place that builds it so
  // "Test" and "Save" cannot disagree about what is sent.
  const buildAuth = () => ({
    auth_type: authType,
    api_key: apiKey || undefined,
    header_name: authHeaderName || undefined,
    header_prefix: authHeaderPrefix || undefined,
    headers: customHeaders,
    ...(authType === "oauth2"
      ? {
          oauth_authorization_url: oauthAuthUrl.trim() || undefined,
          oauth_token_url: oauthTokenUrl.trim() || undefined,
          oauth_client_id: oauthClientId.trim() || undefined,
          oauth_client_secret: oauthClientSecret.trim() || undefined,
          oauth_scopes: oauthScopes.split(" ").filter(Boolean),
          oauth_mode: formScope === "org" ? oauthMode : "org",
        }
      : {}),
  });

  const handleValidateConnection = async () => {
    if (!endpoint) {
      setValidationResult({ valid: false, message: "Please provide an endpoint URL or command" });
      return;
    }
    setValidating(true);
    setValidationResult(null);
    try {
      const payload = {
        transport,
        endpoint_or_command: endpoint,
        scope: formScope,
        auth: buildAuth(),
      };

      const res = await fetch("/v1/mcp/servers/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        setValidationResult({ valid: false, message: data.detail || "Validation failed" });
      } else {
        setValidationResult(data);
      }
    } catch (err) {
      setValidationResult({
        valid: false,
        message: err instanceof Error ? err.message : "Validation error occurred",
      });
    } finally {
      setValidating(false);
    }
  };

  const handleSaveServer = async () => {
    if (!serverName.trim() || !endpoint.trim()) {
      setFormError("Name and endpoint are required.");
      return;
    }

    setFormError(null);
    try {
      const payload: Record<string, unknown> = {
        name: serverName.trim(),
        transport,
        endpoint_or_command: endpoint.trim(),
        scope: formScope,
        guidance: guidance.trim() || undefined,
        auth: buildAuth(),
      };

      const res = await fetch("/v1/mcp/servers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to register MCP server");
      }

      const created: McpServerConfig = await res.json();
      onSaved(created, `Registered MCP server '${created.name}' (${formScope} scope)`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error saving MCP server";
      setFormError(msg);
    }
  };

  return (
    <Modal
      title={formScope === "org" ? "Register an organization MCP server" : "Register a personal MCP server"}
      onClose={onCancel}
      testId="mcp-server-modal"
    >
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <Field label="Server name">
                  {(control) => (
                    <Input
                      {...control}
                      placeholder="e.g. Stripe Billing MCP"
                      value={serverName}
                      onChange={(e) => setServerName(e.target.value)}
                    />
                  )}
                </Field>
                <Field label="Transport">
                  {(control) => (
                    <Select
                      {...control}
                      value={transport}
                      onChange={(e) =>
                        setTransport(e.target.value as "http" | "sse" | "streamable_http" | "websocket" | "stdio")
                      }
                    >
                      <option value="http">http (Streamable HTTP / SSE)</option>
                      <option value="sse">sse (Server-Sent Events)</option>
                      <option value="streamable_http">streamable_http (Modern MCP HTTP)</option>
                      <option value="websocket">websocket (Full-Duplex WS)</option>
                      {isAdmin && formScope === "org" && (
                        <option value="stdio">stdio (Admin Subprocess CLI / npx)</option>
                      )}
                    </Select>
                  )}
                </Field>
                <Field label={transport === "stdio" ? "Command" : "Endpoint URL"}>
                  {(control) => (
                    <Input
                      {...control}
                      placeholder={
                        transport === "stdio"
                          ? "npx -y @modelcontextprotocol/server-postgres"
                          : "https://api.internal.service/mcp"
                      }
                      value={endpoint}
                      onChange={(e) => setEndpoint(e.target.value)}
                    />
                  )}
                </Field>
              </div>

              <Disclosure summary="Guidance for the agent (optional)" testId="mcp-guidance-toggle">
                <Field label="When and how to use this server" hint="Added to the agent's instructions whenever these tools are available.">
                  {(control) => (
                    <Textarea
                      {...control}
                      rows={2}
                      placeholder="e.g. Always check the customer currency before creating charges. Never run destructive refunds without confirmation."
                      value={guidance}
                      onChange={(e) => setGuidance(e.target.value)}
                    />
                  )}
                </Field>
              </Disclosure>

              {/* Auth Mode */}
              <div className="pt-2 border-t border-border/60 space-y-3">
                <div className="flex items-center gap-3">
                  <label className="text-xs font-medium text-neutral-600 dark:text-neutral-300">
                    Authentication Mode:
                  </label>
                  <select aria-label="Authentication Mode"
                    value={authType}
                    onChange={(e) => setAuthType(e.target.value)}
                    className="px-2 py-1 rounded border border-border bg-void text-xs font-mono text-cyan focus:outline-none focus:border-cyan"
                  >
                    <option value="none">No Authentication (Public / Local)</option>
                    <option value="api_key">API Key / Bearer Token</option>
                    <option value="custom_headers">Custom Headers (Multi-Secret)</option>
                    <option value="oauth2">OAuth 2.1 (RFC 9728 PKCE)</option>
                  </select>
                </div>

                {authType === "api_key" && (
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 rounded bg-surface-card border border-border">
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Header Name
                      </label>
                      <input aria-label="Header Name"
                        type="text"
                        value={authHeaderName}
                        onChange={(e) => setAuthHeaderName(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Prefix
                      </label>
                      <input aria-label="Prefix"
                        type="text"
                        value={authHeaderPrefix}
                        onChange={(e) => setAuthHeaderPrefix(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Secret API Key (Write-Only)
                      </label>
                      <input aria-label="Secret API Key (Write-Only)"
                        type="password"
                        placeholder="sk_live_..."
                        value={apiKey}
                        onChange={(e) => setApiKey(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                  </div>
                )}

                {authType === "oauth2" && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 p-3 rounded bg-surface-card border border-border">
                    <p className="sm:col-span-2 text-[10px] text-muted-foreground">
                      Leave the URLs and client ID empty to discover them from the server itself
                      (OAuth metadata and dynamic client registration).
                    </p>
                    {formScope === "org" && (
                      <label className="sm:col-span-2 flex items-center gap-2 text-[11px] font-mono text-neutral-300">
                        <input
                          type="checkbox"
                          data-testid="mcp-oauth-member-mode"
                          checked={oauthMode === "member"}
                          onChange={(e) => setOauthMode(e.target.checked ? "member" : "org")}
                        />
                        Each member connects their own account (instead of one shared account)
                      </label>
                    )}
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Authorization URL
                      </label>
                      <input aria-label="Authorization URL"
                        type="text"
                        placeholder="https://auth.example.com/oauth/authorize"
                        value={oauthAuthUrl}
                        onChange={(e) => setOauthAuthUrl(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Token URL
                      </label>
                      <input aria-label="Token URL"
                        type="text"
                        placeholder="https://auth.example.com/oauth/token"
                        value={oauthTokenUrl}
                        onChange={(e) => setOauthTokenUrl(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Client ID
                      </label>
                      <input aria-label="Client ID"
                        type="text"
                        placeholder="client_123"
                        value={oauthClientId}
                        onChange={(e) => setOauthClientId(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Client Secret (Optional for Public PKCE)
                      </label>
                      <input aria-label="Client Secret (Optional for Public PKCE)"
                        type="password"
                        placeholder="secret_..."
                        value={oauthClientSecret}
                        onChange={(e) => setOauthClientSecret(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Scopes (Space Separated)
                      </label>
                      <input aria-label="Scopes (Space Separated)"
                        type="text"
                        placeholder="read write"
                        value={oauthScopes}
                        onChange={(e) => setOauthScopes(e.target.value)}
                        className="w-full px-2.5 py-1 rounded border border-border bg-surface text-xs font-mono"
                      />
                    </div>
                  </div>
                )}

                {authType === "custom_headers" && (
                  <div className="space-y-2 p-3 rounded bg-surface-card border border-border">
                    <div className="text-xs font-medium text-neutral-600 dark:text-neutral-300">
                      Custom Header Pairs
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        placeholder="Header Key (e.g. X-Org-ID)"
                        value={newHeaderKey}
                        onChange={(e) => setNewHeaderKey(e.target.value)}
                        className="flex-1 px-2.5 py-1 rounded border border-border bg-void text-xs font-mono"
                      />
                      <input
                        type={newHeaderSecret ? "password" : "text"}
                        placeholder="Header Value"
                        value={newHeaderVal}
                        onChange={(e) => setNewHeaderVal(e.target.value)}
                        className="flex-1 px-2.5 py-1 rounded border border-border bg-void text-xs font-mono"
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
                        onClick={() => {
                          if (!newHeaderKey || !newHeaderVal) return;
                          setCustomHeaders([
                            ...customHeaders,
                            { name: newHeaderKey, value: newHeaderVal, is_secret: newHeaderSecret },
                          ]);
                          setNewHeaderKey("");
                          setNewHeaderVal("");
                          setNewHeaderSecret(false);
                        }}
                        className="px-2.5 py-1 rounded border border-border text-xs font-mono bg-surface-elevated text-neutral-300"
                      >
                        Add Header
                      </button>
                    </div>
                    {customHeaders.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {customHeaders.map((h, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 rounded bg-surface border border-border text-[10px] font-mono flex items-center gap-1"
                          >
                            <span>{h.name}: {h.is_secret ? "••••••••" : h.value}</span>
                            <button
                              type="button"
                              onClick={() => setCustomHeaders(customHeaders.filter((_, idx) => idx !== i))}
                              className="text-neutral-500 hover:text-rose-400"
                            >
                              ✕
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Validation Feedback */}
                {validationResult && (
                  <div
                    className={`p-3 rounded-lg border text-xs font-mono flex items-start gap-2.5 ${
                      validationResult.valid
                        ? "bg-emerald-950/40 border-emerald-800/60 text-emerald-300"
                        : "bg-rose-950/40 border-rose-800/60 text-rose-300"
                    }`}
                  >
                    {validationResult.valid ? (
                      <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400 mt-0.5" />
                    ) : (
                      <AlertCircle className="w-4 h-4 shrink-0 text-rose-400 mt-0.5" />
                    )}
                    <div>
                      <div className="font-semibold">
                        {validationResult.valid ? "Connection Verified" : "Validation Failed"}
                      </div>
                      <div className="text-[11px] opacity-90 mt-0.5">
                        {validationResult.message ||
                          validationResult.error ||
                          `Discovered ${validationResult.tools_count ?? validationResult.tools?.length ?? 0} active tools.`}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {formError && (
                <p role="alert" data-testid="mcp-form-error" className="text-xs text-rose-600 dark:text-rose-400">
                  {formError}
                </p>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 pt-3 border-t border-border/60">
                <button
                  type="button"
                  onClick={handleValidateConnection}
                  disabled={validating || !endpoint}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-border bg-surface-elevated hover:bg-surface-elevated/80 text-xs font-mono text-neutral-300 transition-all cursor-pointer disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${validating ? "animate-spin text-cyan" : ""}`} />
                  <span>{validating ? "PROBING..." : "TEST & VALIDATE CONNECTION"}</span>
                </button>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onCancel()}
                    className="px-3 py-1.5 rounded border border-border text-xs font-mono text-neutral-400 hover:text-white cursor-pointer"
                  >
                    CANCEL
                  </button>
                  <button
                    type="button"
                    onClick={handleSaveServer}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded border border-cyan/40 bg-cyan text-slate-950 hover:bg-cyan/90 text-xs font-mono font-semibold transition-all cursor-pointer shadow-sm"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Probe &amp; connect server</span>
                  </button>
                </div>
              </div>
    </Modal>
  );
};
