"use client";

import React from "react";
import { Disclosure } from "../../../ui/Disclosure";
import { Field, Input, Select, Textarea } from "../../../ui/Field";
import { EnvEditor } from "../EnvEditor";
import { CustomHeadersFields } from "./CustomHeadersFields";
import { OAuthFields } from "./OAuthFields";
import type { McpServerFormState } from "./useMcpServerForm";

const ApiKeyFields: React.FC<{ form: McpServerFormState }> = ({ form }) => {
  const { auth } = form;
  return (
    <div className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-surface-card p-3 sm:grid-cols-3">
      <Field label="Header Name">
        {(control) => <Input {...control} value={auth.headerName} onChange={(e) => auth.setHeaderName(e.target.value)} />}
      </Field>
      <Field label="Prefix">
        {(control) => <Input {...control} value={auth.headerPrefix} onChange={(e) => auth.setHeaderPrefix(e.target.value)} />}
      </Field>
      <Field label="Secret API Key (Write-Only)">
        {(control) => (
          <Input
            {...control}
            type="password"
            placeholder={form.editing?.has_api_key ? "Unchanged (type to replace)" : "sk_live_..."}
            value={auth.apiKey}
            onChange={(e) => auth.setApiKey(e.target.value)}
          />
        )}
      </Field>
    </div>
  );
};

const CommandFields: React.FC<{ form: McpServerFormState }> = ({ form }) => {
  const { auth, commandBlocker, canUseCommands } = form;
  return (
    <div className="space-y-3">
      {commandBlocker && (
        <p
          data-testid="mcp-command-blocker"
          role="status"
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-xs text-amber-700 dark:text-amber-300"
        >
          {commandBlocker}.{" "}
          {canUseCommands
            ? "Command servers need the MCP runner (mcpRunner.enabled in the Helm chart, the mcp-runner service in Docker Compose)."
            : "They run programs on the platform, so only administrators can add them; you can still use the ones they add."}
        </p>
      )}
      <EnvEditor rows={auth.envRows} onChange={auth.setEnvRows} />
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
              placeholder="curl -fsSL https://example.com/server_linux_amd64.tar.gz | tar xz"
              value={auth.installCommand}
              onChange={(e) => auth.setInstallCommand(e.target.value)}
            />
          )}
        </Field>
      </Disclosure>
    </div>
  );
};

/** Step 3: how a remote server authenticates, or the variables a command server reads. */
export const AuthStep: React.FC<{ form: McpServerFormState }> = ({ form }) => {
  const { auth, isCommand } = form;
  if (isCommand) return <CommandFields form={form} />;
  return (
    <div className="space-y-3">
      <Field label="Authentication Mode" className="max-w-sm">
        {(control) => (
          <Select {...control} value={auth.authType} onChange={(e) => auth.setAuthType(e.target.value)}>
            <option value="none">No Authentication (Public / Local)</option>
            <option value="api_key">API Key / Bearer Token</option>
            <option value="custom_headers">Custom Headers (Multi-Secret)</option>
            <option value="oauth2">OAuth 2.1 (RFC 9728 PKCE)</option>
          </Select>
        )}
      </Field>
      {auth.authType === "api_key" && <ApiKeyFields form={form} />}
      {auth.authType === "oauth2" && <OAuthFields form={form} />}
      {auth.authType === "custom_headers" && <CustomHeadersFields form={form} />}
    </div>
  );
};
