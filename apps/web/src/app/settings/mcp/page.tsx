"use client";

import { Button } from "../../../components/ui/Button";
import { useConfirm } from "../../../components/ui/ConfirmDialog";
import { PageHeader } from "../../../components/ui/PageHeader";



import React, { useState, useEffect, useCallback } from "react";
import { McpRunnerStatus, McpServerConfig } from "../../../types/mission";
import { Server, Plus, Shield, CheckCircle2, AlertCircle } from "lucide-react";
import { McpServerCard } from "../../../components/settings/mcp/McpServerCard";
import { McpServerForm } from "../../../components/settings/mcp/McpServerForm";
import { SchemaSettingsForm } from "../../../components/settings/SchemaSettingsForm";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
  TeamScopeSection,
} from "../../../components/settings/ScopedSettingsSection";
import { useMoveConfirmation } from "../../../components/settings/Owner";
import { hasCapability } from "../../../lib/access";
import { useOwnedSections } from "../../../lib/useOwnedSections";
import { ownerKey, type OwnerKey } from "../../../lib/ownership";

const SIGN_IN_MESSAGE = "rocket-mcp-sign-in";

export default function McpSettingsPage() {
  const [confirm, confirmDialog] = useConfirm();
  const [confirmMove, moveDialog] = useMoveConfirmation();

  const [servers, setServers] = useState<McpServerConfig[]>([]);
  const [runner, setRunner] = useState<McpRunnerStatus | null>(null);
  const [probingId, setProbingId] = useState<string | null>(null);
  const [expandedServerId, setExpandedServerId] = useState<string | null>(null);

  // The registration form is mounted fresh each time it opens, so it needs no reset.
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServerConfig | null>(null);
  const [formOwner, setFormOwner] = useState<OwnerKey>("me");

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

  // A sign-in finishes in its own tab: that tab reports to the settings page that opened it and
  // closes; reached directly (no opener), it shows the outcome itself.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("status");
    if (!status) return;
    const outcome = { type: SIGN_IN_MESSAGE, status, error: params.get("error") };
    if (window.opener && window.opener !== window) {
      window.opener.postMessage(outcome, window.location.origin);
      window.close();
      return;
    }
    window.history.replaceState(null, "", window.location.pathname);
    showSignInOutcome(outcome.status, outcome.error);
     
  }, []);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.data?.type !== SIGN_IN_MESSAGE) return;
      showSignInOutcome(event.data.status, event.data.error);
      fetchServers();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
     
  }, [fetchServers]);

  const showSignInOutcome = (status: string, error: string | null) => {
    if (status === "connected") {
      setSuccessBanner("Signed in: the server's tools are available to your sessions.");
      setTimeout(() => setSuccessBanner(null), 5000);
    } else {
      setErrorBanner(`Sign-in did not complete: ${error || "the provider refused it"}`);
    }
  };

  useEffect(() => {
    fetch("/v1/mcp/runner")
      .then((res) => (res.ok ? res.json() : null))
      .then((status) => setRunner(status))
      .catch(() => setRunner(null));
  }, []);

  const sections = useOwnedSections(servers);
  const { access } = sections;
  const isAdmin = access.isAdmin;
  // Registering a personal server needs the capability; a team's or the organization's needs managing it.
  const owners = sections.creatable.filter((key) => key !== "me" || hasCapability(access, "register_mcp") || isAdmin);

  const handleOpenAddForm = (owner: OwnerKey) => {
    setFormOwner(owner);
    setShowAddForm(true);
  };

  const showSuccess = (message: string) => {
    setSuccessBanner(message);
    setTimeout(() => setSuccessBanner(null), 4000);
  };

  // A server was added (by the form): list it and check what it exposes.
  const handleServerAdded = (server: McpServerConfig, message: string) => {
    setServers((prev) =>
      prev.some((s) => s.id === server.id)
        ? prev.map((s) => (s.id === server.id ? server : s))
        : [...prev, server]
    );
    setShowAddForm(false);
    setEditingServer(null);
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
    if (!(await confirm({ title: `Remove the MCP server “${name}”?`, message: "Agents lose its tools from their next turn.", confirmLabel: "Remove", tone: "danger" }))) return;
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
    // In a tab of its own: this page stays, and hears back when the sign-in is done.
    window.open(`/v1/mcp/servers/${id}/oauth/authorize`, "_blank");
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
      onEdit={(target) => setEditingServer(target)}
      onAuthorize={handleOAuthAuthorize}
      runner={runner}
      onMemberEnvChanged={fetchServers}
    />
  );

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      {confirmDialog}
      {moveDialog}
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

      {editingServer && (
        <McpServerForm
          key={editingServer.id}
          owner={ownerKey(editingServer)}
          owners={owners}
          confirmMove={confirmMove}
          isAdmin={isAdmin}
          runner={runner}
          editing={editingServer}
          onSaved={handleServerAdded}
          onCancel={() => setEditingServer(null)}
        />
      )}

      {showAddForm && (
        <McpServerForm
          owner={formOwner}
          owners={owners}
          isAdmin={isAdmin}
          runner={runner}
          onSaved={handleServerAdded}
          onCancel={() => setShowAddForm(false)}
        />
      )}


      {sections.mine && (
        <UserScopeSection
          title="Personal MCP Servers"
          description="Connect your private tool providers and API credentials. These tools are available to your sessions and custom agents."
          action={
            <Button size="sm" variant="secondary" onClick={() => handleOpenAddForm("me")}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Add server
            </Button>
          }
        >
          {sections.mine.length > 0 ? (
            <div className="space-y-2.5">{sections.mine.map((s) => renderCard(s, true))}</div>
          ) : (
            <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
              <Server className="w-6 h-6 mx-auto text-neutral-500" />
              <p className="text-xs text-muted-foreground">You haven&apos;t added any personal MCP servers yet.</p>
            </div>
          )}
        </UserScopeSection>
      )}

      {sections.teamSections.map((section) => (
        <React.Fragment key={section.key}>
          <ScopeDivider label={`Team ${section.team.name}`} />
          <TeamScopeSection
            title={`Team · ${section.team.name}`}
            description="Tool providers the team's members use; its administrators manage them."
            canManage={section.canManage}
            action={
              <Button size="sm" variant="secondary" data-testid={`new-team-mcp-${section.team.id}`} onClick={() => handleOpenAddForm(section.key)}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Add team server
              </Button>
            }
          >
            {section.items.length > 0 ? (
              <div className="space-y-2.5">{section.items.map((s) => renderCard(s, section.canManage))}</div>
            ) : (
              <p className="text-xs text-muted-foreground">The team has no MCP server yet.</p>
            )}
          </TeamScopeSection>
        </React.Fragment>
      ))}

      {sections.org && (
        <>
          {(sections.mine || sections.teamSections.length > 0) && <ScopeDivider label="Organization Scope" />}
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
            {sections.org.length > 0 ? (
              <div className="space-y-2.5">{sections.org.map((s) => renderCard(s, isAdmin))}</div>
            ) : (
              <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
                <Shield className="w-6 h-6 mx-auto text-neutral-500" />
                <p className="text-xs text-muted-foreground">No organization-wide MCP servers registered.</p>
              </div>
            )}
          </OrgScopeSection>
        </>
      )}

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
