"use client";

import React from "react";
import { Field, Input, Select } from "../../../ui/Field";
import type { McpServerFormState, Transport } from "./useMcpServerForm";

function transportHint(isAdmin: boolean, scope: "user" | "org"): string | undefined {
  if (!isAdmin) return "Command servers (npx, uvx or a program of your own) are added by an administrator - ask yours.";
  if (scope !== "org") return "Command servers are organization servers: use Add org MCP.";
  return undefined;
}

/** Step 2: the server's name, how it is reached, and its address or command. */
export const ConnectionStep: React.FC<{ form: McpServerFormState }> = ({ form }) => {
  const { isCommand, commandBlocker, canUseCommands } = form;
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <Field label="Server name">
        {(control) => (
          <Input {...control} placeholder="e.g. Stripe Billing MCP" value={form.serverName} onChange={(e) => form.setServerName(e.target.value)} />
        )}
      </Field>
      <Field label="Transport" hint={transportHint(form.isAdmin, form.scope)}>
        {(control) => (
          <Select
            {...control}
            value={form.transport}
            disabled={!!form.editing}
            onChange={(e) => form.changeTransport(e.target.value as Transport)}
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
            ? "Started on the MCP runner for each turn that uses it and stopped when the turn ends. npx and uvx are available; for any other program, give an install command in the next step."
            : undefined
        }
      >
        {(control) => (
          <Input
            {...control}
            placeholder={isCommand ? "npx -y @modelcontextprotocol/server-postgres" : "https://api.internal.service/mcp"}
            value={form.endpoint}
            // The address as typed, not the state: a blur straight after typing can come before the
            // re-render that carries it, and detection would then be skipped.
            onBlur={(e) => void form.detectSignIn(e.currentTarget.value)}
            onChange={(e) => form.changeEndpoint(e.target.value)}
          />
        )}
      </Field>
    </div>
  );
};
