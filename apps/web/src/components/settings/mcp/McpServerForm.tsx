"use client";

import React, { useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Plus,
  RefreshCw,
} from "lucide-react";
import { Field, Input, Select, Textarea } from "../../ui/Field";
import { Disclosure } from "../../ui/Disclosure";
import { Modal } from "../../ui/Modal";
import { MCP_PRESETS, type McpPreset } from "./presets";
import { EnvEditor, type EnvRow } from "./EnvEditor";
import { McpRunnerStatus, McpServerConfig, McpToolSchema } from "../../../types/mission";

interface McpServerFormProps {
  scope: "user" | "org";
  isAdmin: boolean;
  /** Whether command servers can run here; null while it is still being checked. */
  runner: McpRunnerStatus | null;
  onSaved: (server: McpServerConfig, message: string) => void;
  onCancel: () => void;
  /** Edit this registered server instead of registering a new one. */
  editing?: McpServerConfig;
}

const AUTH_OPTIONS = ["none", "api_key", "custom_headers", "oauth2"];

/** The authentication mode the form opens with: a stored server's own, else none. */
function initialAuthType(editing?: McpServerConfig): string {
  if (!editing) return "none";
  if (editing.transport === "stdio") return "custom_headers";
  const stored = editing.auth_type === "oauth" ? "oauth2" : editing.auth_type;
  return stored && AUTH_OPTIONS.includes(stored) ? stored : "none";
}

/** Registers a new MCP server, or edits a registered one: transport, endpoint, credentials, a connection test. */
export const McpServerForm: React.FC<McpServerFormProps> = ({
  scope: formScope,
  isAdmin,
  runner,
  onSaved,
  onCancel,
  editing,
}) => {
  const [validating, setValidating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [preset, setPreset] = useState<McpPreset | null>(null);
  const [validationResult, setValidationResult] = useState<{
    valid: boolean;
    message?: string;
    error?: string | null;
    tools_count?: number;
    tools?: McpToolSchema[];
  } | null>(null);

  // Server Form Fields
  const [serverName, setServerName] = useState(editing?.name ?? "");
  const [transport, setTransport] = useState<"http" | "sse" | "streamable_http" | "websocket" | "stdio">(editing?.transport ?? "http");
  const [endpoint, setEndpoint] = useState(editing?.endpoint_or_command ?? "");
  const [guidance, setGuidance] = useState(editing?.guidance ?? "");
  const [authType, setAuthType] = useState<string>(initialAuthType(editing));
  const [apiKey, setApiKey] = useState("");
  const [authHeaderName, setAuthHeaderName] = useState(editing?.header_name || "Authorization");
  const [authHeaderPrefix, setAuthHeaderPrefix] = useState(editing?.header_prefix ?? "Bearer");
  const [oauthAuthUrl, setOauthAuthUrl] = useState(editing?.oauth_authorization_url ?? "");
  const [oauthTokenUrl, setOauthTokenUrl] = useState(editing?.oauth_token_url ?? "");
  const [oauthClientId, setOauthClientId] = useState(editing?.oauth_client_id ?? "");
  const [oauthClientSecret, setOauthClientSecret] = useState("");
  const [oauthScopes, setOauthScopes] = useState((editing?.oauth_scopes ?? []).join(" "));
  const [oauthMode, setOauthMode] = useState<"org" | "member">(editing?.oauth_mode ?? "org");
  const [customHeaders, setCustomHeaders] = useState<
    Array<{ name: string; value: string; is_secret: boolean; stored?: boolean }>
  >(() =>
    editing && editing.transport !== "stdio"
      ? (editing.headers_preview ?? []).map((h) => ({
          name: h.name,
          // A stored secret is never sent to the page; untouched, it is kept as it is.
          value: h.is_secret ? "" : h.value,
          is_secret: h.is_secret,
          stored: h.is_secret,
        }))
      : []
  );
  const [envRows, setEnvRows] = useState<EnvRow[]>(() =>
    (editing?.headers_preview ?? []).map((h) => ({
      name: h.name,
      // A stored secret is never sent to the page; empty keeps it, typing replaces it.
      value: h.is_secret ? "" : h.value,
      is_secret: h.is_secret,
      member_override: h.member_override ?? false,
      stored: h.is_secret && !!h.value,
    }))
  );
  const [installCommand, setInstallCommand] = useState(editing?.install_command ?? "");
  const isCommand = transport === "stdio";
  const [newHeaderKey, setNewHeaderKey] = useState("");
  const [newHeaderVal, setNewHeaderVal] = useState("");
  const [newHeaderSecret, setNewHeaderSecret] = useState(false);

  // The API takes credentials nested under `auth`; this is the single place that builds it so
  // "Test" and "Save" cannot disagree about what is sent.
  const buildCommandAuth = () => {
    const rows = envRows.filter((row) => row.name.trim());
    return {
      auth_type: rows.length ? "custom_headers" : "none",
      headers: rows.map((row) => ({
        name: row.name.trim(),
        // null = keep what is stored; an empty string = no value (members provide it).
        value: row.stored && row.value === "" ? null : row.value,
        is_secret: row.is_secret,
        member_override: row.member_override,
      })),
      install_command: installCommand.trim() || undefined,
    };
  };

  const buildAuth = () => isCommand ? buildCommandAuth() : ({
    auth_type: authType,
    api_key: apiKey || undefined,
    header_name: authHeaderName || undefined,
    header_prefix: authHeaderPrefix || undefined,
    headers: customHeaders.map((h) => ({
      name: h.name,
      // null = keep what is stored; an empty string never means "keep".
      value: h.stored && h.value === "" ? null : h.value,
      is_secret: h.is_secret,
    })),
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

  const canUseCommands = isAdmin && formScope === "org";
  const runnerReady = runner?.available === true;
  const commandBlocker = !canUseCommands
    ? "Command servers are set up by an administrator - ask yours to add it"
    : runner && !runnerReady
      ? runner.configured
        ? "The MCP runner is not answering"
        : "The MCP runner is not deployed"
      : null;
  const presets = MCP_PRESETS;

  const applyPreset = (id: string) => {
    const chosen = MCP_PRESETS.find((p) => p.id === id) ?? null;
    setPreset(chosen);
    setValidationResult(null);
    if (!chosen) return;
    setServerName(chosen.name);
    setTransport(chosen.transport);
    setEndpoint(chosen.endpoint);
    setAuthType(chosen.auth);
    setCustomHeaders([]);
    setEnvRows(
      (chosen.env ?? []).map((v) => ({
        name: v.name,
        value: "",
        is_secret: v.secret ?? false,
        member_override: v.memberOverride ?? false,
      }))
    );
    setInstallCommand(chosen.install ?? "");
    setOauthAuthUrl("");
    setOauthTokenUrl("");
    setOauthScopes("");
  };

  const handleValidateConnection = async () => {
    if (!endpoint) {
      setValidationResult({ valid: false, message: "Please provide an endpoint URL or command" });
      return;
    }
    setValidating(true);
    setValidationResult(null);
    try {
      const payload = {
        id: editing?.id,
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
        id: editing?.id,
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
      onSaved(
        created,
        editing
          ? `Updated MCP server '${created.name}'`
          : `Registered MCP server '${created.name}' (${formScope} scope)`
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error saving MCP server";
      setFormError(msg);
    }
  };

  return (
    <Modal
      title={
        editing
          ? `Edit ${editing.name}`
          : formScope === "org"
            ? "Register an organization MCP server"
            : "Register a personal MCP server"
      }
      onClose={onCancel}
      testId="mcp-server-modal"
    >
              {!editing && <Field label="Start from a common server (optional)" hint="Fills in the fields below; you can change anything.">
                {(control) => (
                  <Select {...control} data-testid="mcp-preset" value={preset?.id ?? ""} onChange={(e) => applyPreset(e.target.value)}>
                    <option value="">Custom server</option>
                    <optgroup label="Remote servers">
                      {presets.filter((p) => p.transport !== "stdio").map((p) => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Commands run on the MCP runner">
                      {presets.filter((p) => p.transport === "stdio").map((p) => (
                        <option key={p.id} value={p.id} disabled={commandBlocker !== null}>
                          {p.name}
                        </option>
                      ))}
                    </optgroup>
                  </Select>
                )}
              </Field>}

              {preset && (
                <div data-testid="mcp-preset-note" className="rounded-lg border border-surface-border bg-surface-card p-3 text-xs leading-relaxed text-neutral-600 dark:text-neutral-300">
                  <p>{preset.setup}</p>
                  {preset.auth === "oauth2" && (
                    <p className="mt-1">
                      Leave the OAuth fields empty: Rocket Chat discovers the provider&apos;s endpoints and registers itself
                      automatically. If the provider does not allow that, create an OAuth app there with the redirect URL{" "}
                      <code className="font-mono">{`${typeof window === "undefined" ? "" : window.location.origin}/v1/mcp/oauth/callback`}</code>{" "}
                      and enter its client ID and secret.
                    </p>
                  )}
                  <a href={preset.docsUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-brand hover:underline">
                    Setup guide <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  </a>
                </div>
              )}

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
                <Field
                  label="Transport"
                  hint={
                    !isAdmin
                      ? "Command servers (npx, uvx or a program of your own) are added by an administrator - ask yours."
                      : formScope !== "org"
                        ? "Command servers are organization servers: use Add org MCP."
                        : undefined
                  }
                >
                  {(control) => (
                    <Select
                      {...control}
                      value={transport}
                      disabled={!!editing}
                      onChange={(e) => {
                        const next = e.target.value as "http" | "sse" | "streamable_http" | "websocket" | "stdio";
                        setTransport(next);
                        if (next === "stdio" && authType !== "none" && authType !== "custom_headers") setAuthType("none");
                      }}
                    >
                      <option value="http">http (Streamable HTTP / SSE)</option>
                      <option value="sse">sse (Server-Sent Events)</option>
                      <option value="streamable_http">streamable_http (Modern MCP HTTP)</option>
                      <option value="websocket">websocket (Full-Duplex WS)</option>
                      <option value="stdio" disabled={commandBlocker !== null}>
                        Command (runs on the MCP runner){commandBlocker && !canUseCommands ? " - ask an administrator" : ""}
                      </option>
                    </Select>
                  )}
                </Field>
                <Field
                  label={isCommand ? "Command" : "Endpoint URL"}
                  hint={
                    isCommand
                      ? "Started on the MCP runner for each turn that uses it and stopped when the turn ends. npx and uvx are available; for any other program, give an install command below."
                      : undefined
                  }
                >
                  {(control) => (
                    <Input
                      {...control}
                      placeholder={
                        transport === "stdio"
                          ? "npx -y @modelcontextprotocol/server-postgres"
                          : "https://api.internal.service/mcp"
                      }
                      value={endpoint}
                      onChange={(e) => {
                        setEndpoint(e.target.value);
                        // A new address means a new authorization server: let it be discovered again.
                        if (editing && e.target.value !== editing.endpoint_or_command) {
                          setOauthAuthUrl("");
                          setOauthTokenUrl("");
                          setOauthClientId("");
                        }
                      }}
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
                {!isCommand && <div className="flex items-center gap-3">
                  <label className="text-xs font-medium text-neutral-600 dark:text-neutral-300">
                    {isCommand ? "Environment:" : "Authentication Mode:"}
                  </label>
                  <select aria-label={isCommand ? "Environment" : "Authentication Mode"}
                    value={authType}
                    onChange={(e) => setAuthType(e.target.value)}
                    className="px-2 py-1 rounded-lg border border-border bg-void text-xs text-cyan focus:outline-none focus:border-cyan"
                  >
                    {isCommand ? (
                      <>
                        <option value="none">No environment variables</option>
                        <option value="custom_headers">Environment variables</option>
                      </>
                    ) : (
                      <>
                        <option value="none">No Authentication (Public / Local)</option>
                        <option value="api_key">API Key / Bearer Token</option>
                        <option value="custom_headers">Custom Headers (Multi-Secret)</option>
                        <option value="oauth2">OAuth 2.1 (RFC 9728 PKCE)</option>
                      </>
                    )}
                  </select>
                </div>}

                {!isCommand && authType === "api_key" && (
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 rounded-lg bg-surface-card border border-border">
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Header Name
                      </label>
                      <input aria-label="Header Name"
                        type="text"
                        value={authHeaderName}
                        onChange={(e) => setAuthHeaderName(e.target.value)}
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
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
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Secret API Key (Write-Only)
                      </label>
                      <input aria-label="Secret API Key (Write-Only)"
                        type="password"
                        placeholder={editing?.has_api_key ? "Unchanged (type to replace)" : "sk_live_..."}
                        value={apiKey}
                        onChange={(e) => setApiKey(e.target.value)}
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
                      />
                    </div>
                  </div>
                )}

                {!isCommand && authType === "oauth2" && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 p-3 rounded-lg bg-surface-card border border-border">
                    <p className="sm:col-span-2 text-[10px] text-muted-foreground">
                      Leave the URLs and client ID empty to discover them from the server itself
                      (OAuth metadata and dynamic client registration).
                    </p>
                    {formScope === "org" && (
                      <label className="sm:col-span-2 flex items-center gap-2 text-[11px] text-neutral-300">
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
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
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
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
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
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1">
                        Client Secret (Optional for Public PKCE)
                      </label>
                      <input aria-label="Client Secret (Optional for Public PKCE)"
                        type="password"
                        placeholder={editing?.has_oauth_secret ? "Unchanged (type to replace)" : "secret_..."}
                        value={oauthClientSecret}
                        onChange={(e) => setOauthClientSecret(e.target.value)}
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
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
                        className="w-full px-2.5 py-1 rounded-lg border border-border bg-surface text-xs"
                      />
                    </div>
                  </div>
                )}

                {!isCommand && authType === "custom_headers" && (
                  <div className="space-y-2 p-3 rounded-lg bg-surface-card border border-border">
                    <div className="text-xs font-medium text-neutral-600 dark:text-neutral-300">
                      {isCommand ? "Environment variables" : "Custom Header Pairs"}
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        placeholder={isCommand ? "Variable name (e.g. GITHUB_TOKEN)" : "Header Key (e.g. X-Org-ID)"}
                        value={newHeaderKey}
                        onChange={(e) => setNewHeaderKey(e.target.value)}
                        className="flex-1 px-2.5 py-1 rounded-lg border border-border bg-void text-xs"
                      />
                      <input
                        type={newHeaderSecret ? "password" : "text"}
                        placeholder={isCommand ? "Value" : "Header Value"}
                        value={newHeaderVal}
                        onChange={(e) => setNewHeaderVal(e.target.value)}
                        className="flex-1 px-2.5 py-1 rounded-lg border border-border bg-void text-xs"
                      />
                      <label className="flex items-center gap-1 text-[11px] text-muted-foreground cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={newHeaderSecret}
                          onChange={(e) => setNewHeaderSecret(e.target.checked)}
                          className="rounded-lg border-border"
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
                        className="px-2.5 py-1 rounded-lg border border-border text-xs bg-surface-elevated text-neutral-300"
                      >
                        {isCommand ? "Add variable" : "Add Header"}
                      </button>
                    </div>
                    {customHeaders.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {customHeaders.map((h, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 rounded-lg bg-surface border border-border text-[10px] flex items-center gap-1"
                          >
                            <span>{h.name}{isCommand ? "=" : ": "}{h.is_secret ? "••••••••" : h.value}</span>
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

                {isCommand && (
                  <div className="space-y-3">
                    {commandBlocker && (
                      <p data-testid="mcp-command-blocker" role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-xs text-amber-700 dark:text-amber-300">
                        {commandBlocker}.{" "}
                        {canUseCommands
                          ? "Command servers need the MCP runner (mcpRunner.enabled in the Helm chart, the mcp-runner service in Docker Compose)."
                          : "They run programs on the platform, so only administrators can add them; you can still use the ones they add."}
                      </p>
                    )}
                    <EnvEditor rows={envRows} onChange={setEnvRows} />
                    <Disclosure summary="Install command (for programs that are not npx or uvx packages)" testId="mcp-install-toggle">
                      <Field
                        label="Install script"
                        hint="Runs once on the runner (and again only if you change it) to provide the program the command starts, for example downloading a release binary. It runs without this server's variables; the program is then on the command's PATH."
                      >
                        {(control) => (
                          <Textarea
                            {...control}
                            data-testid="mcp-install-command"
                            rows={4}
                            className="font-mono text-xs"
                            placeholder={'curl -fsSL https://example.com/server_linux_amd64.tar.gz | tar xz'}
                            value={installCommand}
                            onChange={(e) => setInstallCommand(e.target.value)}
                          />
                        )}
                      </Field>
                    </Disclosure>
                  </div>
                )}

                {/* Validation Feedback */}
                {validationResult && (
                  <div
                    className={`p-3 rounded-lg border text-xs flex items-start gap-2.5 ${
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
                  disabled={validating || !endpoint || (isCommand && commandBlocker !== null)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface-elevated hover:bg-surface-elevated/80 text-xs text-neutral-300 transition-all cursor-pointer disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${validating ? "animate-spin text-cyan" : ""}`} />
                  <span>{validating ? "PROBING..." : "TEST & VALIDATE CONNECTION"}</span>
                </button>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onCancel()}
                    className="px-3 py-1.5 rounded-lg border border-border text-xs text-neutral-400 hover:text-white cursor-pointer"
                  >
                    CANCEL
                  </button>
                  <button
                    type="button"
                    onClick={handleSaveServer}
                    disabled={isCommand && commandBlocker !== null}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg border border-cyan/40 bg-cyan text-slate-950 hover:bg-cyan/90 text-xs font-semibold transition-all cursor-pointer shadow-sm"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Probe &amp; connect server</span>
                  </button>
                </div>
              </div>
    </Modal>
  );
};
