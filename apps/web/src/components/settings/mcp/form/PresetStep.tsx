"use client";

import React from "react";
import { ExternalLink } from "lucide-react";
import { Field, Select } from "../../../ui/Field";
import { MCP_PRESETS } from "../presets";
import type { McpServerFormState } from "./useMcpServerForm";

export function oauthCallbackUrl(): string {
  return `${typeof window === "undefined" ? "" : window.location.origin}/v1/mcp/oauth/callback`;
}

/** Step 1 (new servers only): start from a common server, or from nothing. */
export const PresetStep: React.FC<{ form: McpServerFormState }> = ({ form }) => {
  const { preset, commandBlocker } = form;
  return (
    <>
      <Field label="Start from a common server (optional)" hint="Fills in the fields below; you can change anything.">
        {(control) => (
          <Select {...control} data-testid="mcp-preset" value={preset?.id ?? ""} onChange={(e) => form.applyPreset(e.target.value)}>
            <option value="">Custom server</option>
            <optgroup label="Remote servers">
              {MCP_PRESETS.filter((p) => p.transport !== "stdio").map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </optgroup>
            <optgroup label="Commands run on the MCP runner">
              {MCP_PRESETS.filter((p) => p.transport === "stdio").map((p) => (
                <option key={p.id} value={p.id} disabled={commandBlocker !== null}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          </Select>
        )}
      </Field>

      {preset && (
        <div
          data-testid="mcp-preset-note"
          className="rounded-lg border border-surface-border bg-surface-card p-3 text-xs leading-relaxed text-neutral-600 dark:text-neutral-300"
        >
          <p>{preset.setup}</p>
          {preset.auth === "oauth2" && (
            <p className="mt-1">
              Leave the OAuth fields empty: Rocket Chat discovers the provider&apos;s endpoints and registers itself
              automatically. If the provider does not allow that, create an OAuth app there with the redirect URL{" "}
              <code className="font-mono">{oauthCallbackUrl()}</code> and enter its client ID and secret.
            </p>
          )}
          <a href={preset.docsUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-brand hover:underline">
            Setup guide <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </a>
        </div>
      )}
    </>
  );
};
