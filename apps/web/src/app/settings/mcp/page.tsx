"use client";

import { Button } from "../../../components/ui/Button";
import { PageHeader } from "../../../components/ui/PageHeader";



import React, { useState, useEffect, useCallback } from "react";
import { McpRunnerStatus, McpServerConfig } from "../../../types/mission";
import { Server, Plus, Shield, CheckCircle2, AlertCircle } from "lucide-react";
import { useSession } from "../../../components/AuthProvider";
import { McpServerCard } from "../../../components/settings/mcp/McpServerCard";
import { McpServerForm } from "../../../components/settings/mcp/McpServerForm";
import { SchemaSettingsForm } from "../../../components/settings/SchemaSettingsForm";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../components/settings/ScopedSettingsSection";

export default function McpSettingsPage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  const [servers, setServers] = useState<McpServerConfig[]>([]);
  const [runner, setRunner] = useState<McpRunnerStatus | null>(null);
  const [probingId, setProbingId] = useState<string | null>(null);
  const [expandedServerId, setExpandedServerId] = useState<string | null>(null);

  // The registration form is mounted fresh each time it opens, so it needs no reset.
  const [showAddForm, setShowAddForm] = useState(false);
  const [formScope, setFormScope] = useState<"user" | "org">("user");

  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);

  const fetchServers = useCallback(async () => {
    try {
      const res = await fetch("/v1/mcp/servers");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          setServers(data);
          if (data.length > 0 && !expandedServerId) {
            setExpandedServerId(data[0].id);
          }
        }
      }
    } catch (err) {
      console.warn("Failed fetching MCP servers:", err);
    }
  }, [expandedServerId]);

  useEffect(() => {
    fetchServers();
  }, [fetchServers]);

  useEffect(() => {
    fetch("/v1/mcp/runner")
      .then((res) => (res.ok ? res.json() : null))
      .then((status) => setRunner(status))
      .catch(() => setRunner(null));
  }, []);

  const userServers = servers.filter((s) => s.scope === "user" || !s.scope || s.user_id != null);
  const orgServers = servers.filter((s) => s.scope === "org" && s.user_id == null);

  const handleOpenAddForm = (scope: "user" | "org") => {
    setFormScope(scope);
    setShowAddForm(true);
  };

  const showSuccess = (message: string) => {
    setSuccessBanner(message);
    setTimeout(() => setSuccessBanner(null), 4000);
  };

  // A server was added (by the form): list it and check what it exposes.
  const handleServerAdded = (server: McpServerConfig, message: string) => {
    setServers((prev) => [...prev, server]);
    setShowAddForm(false);
    showSuccess(message);
    handleProbe(server.id);
  };

  const handleProbe = async (id: string) => {
    setProbingId(id);
    try {
      const res = await fetch(`/v1/mcp/servers/${id}/probe`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Probe failed");
      }
      setServers((prev) =>
        prev.map((s) =>
          s.id === id
            ? {
                ...s,
                status: data.status || "connected",
                last_probed: data.last_probed || "Just now",
                tools_count: data.tools_count ?? s.tools_count,
                tools: data.tools || s.tools,
              }
            : s
        )
      );
      setSuccessBanner(`Probed '${data.name || id}': ${data.tools_count || 0} tools active`);
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Live probe failed";
      setErrorBanner(msg);
      setTimeout(() => setErrorBanner(null), 5000);
    } finally {
      setProbingId(null);
    }
  };

  const handleDeleteServer = async (id: string, name: string) => {
    if (!confirm(`Are you sure you want to remove MCP server '${name}'?`)) return;
    try {
      const res = await fetch(`/v1/mcp/servers/${id}`, { method: "DELETE" });
      if (res.ok) {
        setServers((prev) => prev.filter((s) => s.id !== id));
        setSuccessBanner(`Removed server '${name}'`);
        setTimeout(() => setSuccessBanner(null), 3000);
      } else {
        const err = await res.json().catch(() => ({}));
        setErrorBanner(err.detail || "Failed to delete MCP server");
      }
    } catch {
      setErrorBanner("Failed to delete MCP server");
    }
  };

  const handleOAuthAuthorize = (id: string) => {
    window.location.href = `/v1/mcp/servers/${id}/oauth/authorize`;
  };

  const renderCard = (server: McpServerConfig, canManage: boolean) => (
    <McpServerCard
      key={server.id}
      server={server}
      canManage={canManage}
      expanded={expandedServerId === server.id}
      probing={probingId === server.id}
      onToggle={() => setExpandedServerId(expandedServerId === server.id ? null : server.id)}
      onProbe={handleProbe}
      onDelete={handleDeleteServer}
      onAuthorize={handleOAuthAuthorize}
      runner={runner}
      onMemberEnvChanged={fetchServers}
    />
  );

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      <PageHeader
        title="MCP servers"
        icon={<Server className="h-5 w-5" aria-hidden="true" />}
        description="Connect tool providers over HTTP, SSE or as a command. Secrets are encrypted and never shown again."
      />

      {errorBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorBanner}</span>
        </div>
      )}

      {successBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successBanner}</span>
        </div>
      )}

      {showAddForm && (
        <McpServerForm
          scope={formScope}
          isAdmin={isAdmin}
          runner={runner}
          onSaved={handleServerAdded}
          onCancel={() => setShowAddForm(false)}
        />
      )}


      {/* TIER 1: USER-SPECIFIC CONFIGURATION */}
      <UserScopeSection
        title="Personal MCP Servers"
        description="Connect your private tool providers and API credentials. These tools are available to your sessions and custom agents."
        action={
          <Button size="sm" variant="secondary" onClick={() => handleOpenAddForm("user")}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Add server
              </Button>
        }
      >
        {userServers.length > 0 ? (
          <div className="space-y-2.5">
            {userServers.map((s) => renderCard(s, true))}
          </div>
        ) : (
          <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
            <Server className="w-6 h-6 mx-auto text-neutral-500" />
            <p className="text-xs text-muted-foreground">
              You haven&apos;t added any personal MCP servers yet.
            </p>
          </div>
        )}
      </UserScopeSection>

      {/* VISUAL SEPARATOR */}
      <ScopeDivider label="Organization Scope" />

      {/* TIER 2: ORGANIZATION-WIDE CONFIGURATION */}
      <OrgScopeSection
        title="Organization MCP Servers"
        description="Shared tool providers accessible to all organization members and default fleet agents."
        action={
          <Button size="sm" variant="secondary" onClick={() => handleOpenAddForm("org")}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Add org server
              </Button>
        }
      >
        {orgServers.length > 0 ? (
          <div className="space-y-2.5">
            {orgServers.map((s) => renderCard(s, isAdmin))}
          </div>
        ) : (
          <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
            <Shield className="w-6 h-6 mx-auto text-neutral-500" />
            <p className="text-xs text-muted-foreground">
              No organization-wide MCP servers registered.
            </p>
          </div>
        )}
      </OrgScopeSection>

      {isAdmin && (
        <SchemaSettingsForm
          domain="mcp"
          mode="org"
          title="MCP policy"
          description="Which transports members may use and how many personal servers each may register."
        />
      )}
    </div>
  );
}
