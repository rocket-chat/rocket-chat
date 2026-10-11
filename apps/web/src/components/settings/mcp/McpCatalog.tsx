"use client";

import React, { useState } from "react";
import { ExternalLink, Globe, Shield, Zap } from "lucide-react";
import { Badge } from "../../ui/Badge";
import { McpServerConfig } from "../../../types/mission";

export interface McpCatalogEntry {
  id: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  docs_url?: string | null;
  transport: string;
  endpoint_or_command: string;
  authorization_url?: string | null;
  token_url?: string | null;
  default_scopes: string[];
  supported_auth_types: string[];
  recommended_scope: string;
  supports_org_wide_auth: boolean;
  supports_per_user_oauth: boolean;
  guidance: string;
}

interface McpCatalogProps {
  catalog: McpCatalogEntry[];
  isAdmin: boolean;
  /** Called with a connector installed for the whole organization, and a message to show. */
  onInstalled: (server: McpServerConfig, message: string) => void;
  onError: (message: string) => void;
}

/** One-click connectors: per member (OAuth) or, for administrators, shared with a service key. */
export const McpCatalog: React.FC<McpCatalogProps> = ({ catalog, isAdmin, onInstalled, onError }) => {
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [catalogOrgEntryId, setCatalogOrgEntryId] = useState<string | null>(null);
  const [catalogApiKey, setCatalogApiKey] = useState("");

  const handleInstallCatalogPerUser = async (entry: McpCatalogEntry) => {
    setInstallingId(entry.id);
    try {
      const res = await fetch(`/v1/mcp/catalog/${entry.id}/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "user", auth_type: "oauth2" }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to install connector");
      }
      const created: McpServerConfig = await res.json();
      window.location.href = `/v1/mcp/servers/${created.id}/oauth/authorize`;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Install failed";
      onError(msg);
    } finally {
      setInstallingId(null);
    }
  };

  const handleInstallCatalogOrgWide = async (entry: McpCatalogEntry) => {
    setInstallingId(entry.id);
    try {
      const res = await fetch(`/v1/mcp/catalog/${entry.id}/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope: "org",
          auth_type: "bearer",
          api_key: catalogApiKey || undefined,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to install connector");
      }
      const created: McpServerConfig = await res.json();
      setCatalogOrgEntryId(null);
      setCatalogApiKey("");
      onInstalled(created, `Installed '${created.name}' for the organization`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Install failed";
      onError(msg);
    } finally {
      setInstallingId(null);
    }
  };

  if (catalog.length === 0) return null;

  return (
    <div className="space-y-4 rounded-xl border border-surface-border bg-surface-card p-5">
              <div>
                <div className="font-display text-base font-semibold text-foreground flex items-center gap-2">
                  <Globe className="w-4 h-4 text-cyan" />
                  Connector catalog
                </div>
                <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">
                  One-click onboarding for Jira, Notion, Google Drive, and more. Install
                  org-wide with a shared service credential (usable by the Slack bot and any
                  agent) or connect your own account via OAuth.
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {catalog.map((entry) => (
                  <div
                    key={entry.id}
                    className="space-y-2.5 rounded-lg border border-surface-border bg-surface-base p-3.5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
                          <Zap className="w-3.5 h-3.5 text-brand" />
                          {entry.name}
                        </div>
                        <p className="mt-1 text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                          {entry.description}
                        </p>
                      </div>
                      <Badge className="shrink-0">{entry.category}</Badge>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {entry.supports_org_wide_auth && (
                        <Badge tone="success">Org-wide</Badge>
                      )}
                      {entry.supports_per_user_oauth && (
                        <Badge tone="brand">Per-user</Badge>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      {entry.supports_per_user_oauth && (
                        <button
                          type="button"
                          onClick={() => handleInstallCatalogPerUser(entry)}
                          disabled={installingId === entry.id}
                          className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-brand/40 bg-brand/10 px-2.5 py-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand/20 disabled:opacity-50"
                        >
                          <ExternalLink className="w-3 h-3" />
                          <span>Connect my account</span>
                        </button>
                      )}
                      {isAdmin && entry.supports_org_wide_auth && (
                        <button
                          type="button"
                          onClick={() =>
                            setCatalogOrgEntryId(catalogOrgEntryId === entry.id ? null : entry.id)
                          }
                          className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-surface-border bg-surface-card px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-surface-elevated"
                        >
                          <Shield className="w-3 h-3" />
                          <span>Install org-wide</span>
                        </button>
                      )}
                    </div>
                    {catalogOrgEntryId === entry.id && (
                      <div className="flex items-center gap-2 pt-1">
                        <input
                          type="password"
                          placeholder="Service API key / bearer token"
                          value={catalogApiKey}
                          onChange={(e) => setCatalogApiKey(e.target.value)}
                          className="flex-1 px-2 py-1 rounded border border-border bg-void text-[11px] font-mono text-foreground focus:outline-none focus:border-cyan"
                        />
                        <button
                          type="button"
                          onClick={() => handleInstallCatalogOrgWide(entry)}
                          disabled={installingId === entry.id}
                          className="px-2.5 py-1 rounded bg-cyan/10 hover:bg-cyan/20 border border-cyan/40 text-cyan text-[10px] font-mono font-semibold cursor-pointer disabled:opacity-50"
                        >
                          SAVE
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
  );
};
