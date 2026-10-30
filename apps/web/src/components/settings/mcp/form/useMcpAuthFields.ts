"use client";

import { useState } from "react";
import type { McpServerConfig } from "../../../../types/mission";
import type { EnvRow } from "../EnvEditor";
import type { McpPreset } from "../presets";

export const AUTH_OPTIONS = ["none", "api_key", "custom_headers", "oauth2"];

export interface HeaderRow {
  name: string;
  value: string;
  is_secret: boolean;
  /** Editing: a secret is stored (never sent to the page); left empty, it is kept. */
  stored?: boolean;
}

export interface OAuthFields {
  authorizationUrl: string;
  tokenUrl: string;
  clientId: string;
  clientSecret: string;
  scopes: string;
  mode: "org" | "member";
}

/** The authentication mode the form opens with: a stored server's own, else none. */
function initialAuthType(editing?: McpServerConfig): string {
  if (!editing) return "none";
  if (editing.transport === "stdio") return "custom_headers";
  const stored = editing.auth_type === "oauth" ? "oauth2" : editing.auth_type;
  return stored && AUTH_OPTIONS.includes(stored) ? stored : "none";
}

function initialOAuth(editing?: McpServerConfig): OAuthFields {
  return {
    authorizationUrl: editing?.oauth_authorization_url ?? "",
    tokenUrl: editing?.oauth_token_url ?? "",
    clientId: editing?.oauth_client_id ?? "",
    clientSecret: "",
    scopes: (editing?.oauth_scopes ?? []).join(" "),
    mode: editing?.oauth_mode ?? "org",
  };
}

function initialHeaders(editing?: McpServerConfig): HeaderRow[] {
  if (!editing || editing.transport === "stdio") return [];
  return (editing.headers_preview ?? []).map((h) => ({
    name: h.name,
    value: h.is_secret ? "" : h.value,
    is_secret: h.is_secret,
    stored: h.is_secret,
  }));
}

function initialEnv(editing?: McpServerConfig): EnvRow[] {
  return (editing?.headers_preview ?? []).map((h) => ({
    name: h.name,
    value: h.is_secret ? "" : h.value,
    is_secret: h.is_secret,
    member_override: h.member_override ?? false,
    stored: h.is_secret && !!h.value,
  }));
}

/** The credentials part of the MCP server form: remote sign-in, headers, or a command's variables. */
export function useMcpAuthFields(editing: McpServerConfig | undefined, formScope: "user" | "org") {
  const [authType, setAuthType] = useState<string>(initialAuthType(editing));
  const [apiKey, setApiKey] = useState("");
  const [headerName, setHeaderName] = useState(editing?.header_name || "Authorization");
  const [headerPrefix, setHeaderPrefix] = useState(editing?.header_prefix ?? "Bearer");
  const [oauth, setOauth] = useState<OAuthFields>(() => initialOAuth(editing));
  const [customHeaders, setCustomHeaders] = useState<HeaderRow[]>(() => initialHeaders(editing));
  const [envRows, setEnvRows] = useState<EnvRow[]>(() => initialEnv(editing));
  const [installCommand, setInstallCommand] = useState(editing?.install_command ?? "");

  const patchOauth = (changes: Partial<OAuthFields>) => setOauth((current) => ({ ...current, ...changes }));

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

  const buildRemoteAuth = () => ({
    auth_type: authType,
    api_key: apiKey || undefined,
    header_name: headerName || undefined,
    header_prefix: headerPrefix || undefined,
    headers: customHeaders.map((h) => ({
      name: h.name,
      // null = keep what is stored; an empty string never means "keep".
      value: h.stored && h.value === "" ? null : h.value,
      is_secret: h.is_secret,
    })),
    ...(authType === "oauth2"
      ? {
          oauth_authorization_url: oauth.authorizationUrl.trim() || undefined,
          oauth_token_url: oauth.tokenUrl.trim() || undefined,
          oauth_client_id: oauth.clientId.trim() || undefined,
          oauth_client_secret: oauth.clientSecret.trim() || undefined,
          oauth_scopes: oauth.scopes.split(" ").filter(Boolean),
          oauth_mode: formScope === "org" ? oauth.mode : "org",
        }
      : {}),
  });

  // The single place that builds the API's nested `auth`, so "Test" and "Save" cannot disagree.
  const buildAuth = (isCommand: boolean) => (isCommand ? buildCommandAuth() : buildRemoteAuth());

  const applyPreset = (chosen: McpPreset) => {
    setAuthType(chosen.auth);
    setCustomHeaders([]);
    setEnvRows(
      (chosen.env ?? []).map((v) => ({
        name: v.name,
        value: "",
        is_secret: v.secret ?? false,
        member_override: v.memberOverride ?? false,
      })),
    );
    setInstallCommand(chosen.install ?? "");
    patchOauth({ authorizationUrl: "", tokenUrl: "", scopes: "" });
  };

  /** A new address means a new authorization server: let it be discovered again. */
  const forgetDiscoveredOAuth = () => patchOauth({ authorizationUrl: "", tokenUrl: "", clientId: "" });

  return {
    authType,
    setAuthType,
    apiKey,
    setApiKey,
    headerName,
    setHeaderName,
    headerPrefix,
    setHeaderPrefix,
    oauth,
    patchOauth,
    customHeaders,
    setCustomHeaders,
    envRows,
    setEnvRows,
    installCommand,
    setInstallCommand,
    buildAuth,
    applyPreset,
    forgetDiscoveredOAuth,
  };
}

export type McpAuthFields = ReturnType<typeof useMcpAuthFields>;
