"use client";

import React from "react";
import { Disclosure } from "../../../ui/Disclosure";
import { Field, Input } from "../../../ui/Field";
import { oauthCallbackUrl } from "./PresetStep";
import type { McpServerFormState } from "./useMcpServerForm";

/** An OAuth server: who signs in, and (rarely) an OAuth app created on the provider. */
export const OAuthFields: React.FC<{ form: McpServerFormState }> = ({ form }) => {
  const { oauth, patchOauth } = form.auth;
  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface-card p-3">
      <p data-testid="mcp-sign-in-summary" className="text-xs text-foreground">
        {form.signIn === "needs_client"
          ? "This server signs you in with your account, but its provider does not let applications register themselves: create an OAuth app there and enter it under Advanced."
          : "This server signs you in with your account. Nothing to set up: save it and sign in in the tab that opens."}
      </p>
      {form.scope === "org" && (
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-xs font-medium text-neutral-600 dark:text-neutral-300">Who signs in</legend>
          <label className="flex items-center gap-2 text-xs text-foreground">
            <input
              type="radio"
              name="mcp-oauth-mode"
              data-testid="mcp-oauth-member-mode"
              checked={oauth.mode === "member"}
              onChange={() => patchOauth({ mode: "member" })}
            />
            Each member with their own account
          </label>
          <label className="flex items-center gap-2 text-xs text-foreground">
            <input
              type="radio"
              name="mcp-oauth-mode"
              data-testid="mcp-oauth-shared-mode"
              checked={oauth.mode === "org"}
              onChange={() => patchOauth({ mode: "org" })}
            />
            One shared account for the organization (you sign in now)
          </label>
        </fieldset>
      )}
      <Disclosure
        summary="Advanced: an OAuth app created on the provider"
        testId="mcp-oauth-advanced"
        defaultOpen={form.signIn === "needs_client" || !!(oauth.clientId || oauth.authorizationUrl)}
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <p className="text-[10px] text-muted-foreground sm:col-span-2">
            Only for a provider that does not let applications register themselves. Its callback URL is{" "}
            <code className="font-mono">{oauthCallbackUrl()}</code>.
          </p>
          <Field label="Authorization URL">
            {(control) => (
              <Input
                {...control}
                placeholder="https://auth.example.com/oauth/authorize"
                value={oauth.authorizationUrl}
                onChange={(e) => patchOauth({ authorizationUrl: e.target.value })}
              />
            )}
          </Field>
          <Field label="Token URL">
            {(control) => (
              <Input
                {...control}
                placeholder="https://auth.example.com/oauth/token"
                value={oauth.tokenUrl}
                onChange={(e) => patchOauth({ tokenUrl: e.target.value })}
              />
            )}
          </Field>
          <Field label="Client ID">
            {(control) => (
              <Input {...control} placeholder="client_123" value={oauth.clientId} onChange={(e) => patchOauth({ clientId: e.target.value })} />
            )}
          </Field>
          <Field label="Client Secret (Optional for Public PKCE)">
            {(control) => (
              <Input
                {...control}
                type="password"
                placeholder={form.editing?.has_oauth_secret ? "Unchanged (type to replace)" : "secret_..."}
                value={oauth.clientSecret}
                onChange={(e) => patchOauth({ clientSecret: e.target.value })}
              />
            )}
          </Field>
          <Field label="Scopes (Space Separated)" className="sm:col-span-2">
            {(control) => (
              <Input {...control} placeholder="read write" value={oauth.scopes} onChange={(e) => patchOauth({ scopes: e.target.value })} />
            )}
          </Field>
        </div>
      </Disclosure>
    </div>
  );
};
