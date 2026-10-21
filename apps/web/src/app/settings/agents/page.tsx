"use client";

import { useShallow } from "zustand/react/shallow";
import { Button } from "../../../components/ui/Button";
import { PageHeader } from "../../../components/ui/PageHeader";

import { Field, Input, Select, Textarea } from "../../../components/ui/Field";
import React, { useState, useEffect, useRef, useCallback } from "react";
import { ModelPicker } from "../../../components/ui/ModelPicker";
import { useMissionStore } from "../../../lib/store";
import {
  AgentPersonaConfig,
  McpServerConfig,
  CustomSkillConfig,
  KnowledgeBaseConfig,
} from "../../../types/mission";
import { MarkdownRenderer } from "../../../components/MarkdownRenderer";
import { ToolLoadBadge, ToolLoadSummary } from "../../../components/settings/ToolLoadState";
import { useToolLoadingPreview } from "../../../lib/useToolLoadingPreview";
import { usePersonaLint } from "../../../lib/usePersonaLint";
import {
  Bot,
  Plus,
  Save,
  CheckCircle2,
  Shield,
  Bug,
  Book,
  Check,
  Pin,
  Eye,
  Edit3,
  AlertCircle,
  Server,
  Code2,
  Database,
  Trash2,
  RotateCcw,
  AlertTriangle,
} from "lucide-react";
import { useSession } from "../../../components/AuthProvider";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../components/settings/ScopedSettingsSection";

const getAgentIcon = (iconName?: string) => {
  switch (iconName) {
    case "shield":
      return <Shield className="w-4 h-4 text-emerald-400" />;
    case "server":
      return <Server className="w-4 h-4 text-sky-400" />;
    case "bug":
      return <Bug className="w-4 h-4 text-amber-400" />;
    case "book":
      return <Book className="w-4 h-4 text-purple-400" />;
    case "bot":
    default:
      return <Bot className="w-4 h-4 text-cyan" />;
  }
};

const SYSTEM_TOOLS = [
  { id: "bash_exec", name: "Bash Terminal Exec", description: "Execute commands in isolated container sandbox", badge: "CLI" },
  { id: "file_read", name: "File Reader", description: "Read files, syntax-check and slice line ranges", badge: "FS" },
  { id: "file_write", name: "File Writer", description: "Atomic write and replacement edits", badge: "FS" },
  { id: "edit_block", name: "Surgical Block Editor", description: "Targeted chunk replacements and refactoring", badge: "FS" },
  { id: "apply_patch", name: "Patch Applicator", description: "Apply unified diff patches cleanly", badge: "FS" },
  { id: "git_commit", name: "Git Engine Committer", description: "Stage and commit changes with co-author trailers", badge: "GIT" },
  { id: "web_search", name: "Live Web Search", description: "Search web docs, packages, and references", badge: "NET" },
  { id: "web_fetch", name: "URL Content Ingest", description: "Convert HTML/Markdown from documentation URLs", badge: "NET" },
  { id: "ask_question", name: "Decision Gates", description: "Ask human clarification questions during turns", badge: "INT" },
];

export default function AgentsSettingsPage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  const {
    availableAgents,
    setAvailableAgents,
    activeAgent,
    selectAgent,
    fetchAgents,
  } = useMissionStore(
    useShallow((state) => ({ availableAgents: state.availableAgents, setAvailableAgents: state.setAvailableAgents, activeAgent: state.activeAgent, selectAgent: state.selectAgent, fetchAgents: state.fetchAgents }))
  );

  const [mcpServers, setMcpServers] = useState<McpServerConfig[]>([]);
  const [skillsList, setSkillsList] = useState<CustomSkillConfig[]>([]);
  const [kbList, setKbList] = useState<KnowledgeBaseConfig[]>([]);
  // Real runtime tool catalog (built-in, memory, web, MCP, Slack toolset, …), so every
  // available tool is visible and configurable rather than a stale hard-coded subset.
  const [toolCatalog, setToolCatalog] = useState<
    { name: string; description: string; category: string; toolset: string | null }[]
  >([]);

  const [selectedAgentId, setSelectedAgentId] = useState<string>(
    activeAgent?.id || availableAgents[0]?.id || "persona-general"
  );
  const [formData, setFormData] = useState<AgentPersonaConfig>(
    availableAgents.find((a) => a.id === selectedAgentId) || availableAgents[0] || {
      id: "persona-general",
      name: "General Engineer",
      role_title: "Full-Stack Engineer",
      system_prompt: "You are an autonomous engineering agent.",
      model: "openrouter/deepseek/deepseek-v4.1-flash",
      whitelisted_tools: ["bash_exec", "file_read"],
      scope: "org",
    }
  );
  const [previewMarkdown, setPreviewMarkdown] = useState(false);
  const [orgMaxTurns, setOrgMaxTurns] = useState<number | null>(null);
  const [sandboxTypes, setSandboxTypes] = useState<{ id: string; name: string; is_default: boolean }[]>([]);
  const [isSaved, setIsSaved] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const prevAgentIdRef = useRef<string | null>(null);
  const prevAgentsRef = useRef<AgentPersonaConfig[] | null>(null);

  // Fetch contextual tool registries
  const loadRegistries = useCallback(async () => {
    try {
      const [mcpRes, skillsRes, kbRes, toolsRes, limitsRes, sandboxesRes] = await Promise.all([
        fetch("/v1/mcp/servers"),
        fetch("/v1/skills"),
        fetch("/v1/knowledge/bases"),
        fetch("/v1/tools"),
        fetch("/v1/agents/limits"),
        fetch("/v1/sandboxes"),
      ]);

      if (sandboxesRes.ok) {
        const body = await sandboxesRes.json();
        if (Array.isArray(body?.sandboxes)) setSandboxTypes(body.sandboxes);
      }

      if (limitsRes.ok) {
        const limits = await limitsRes.json();
        if (typeof limits.max_turns === "number") setOrgMaxTurns(limits.max_turns);
      }

      if (mcpRes.ok) {
        const mcps = await mcpRes.json();
        if (Array.isArray(mcps)) setMcpServers(mcps);
      }
      if (skillsRes.ok) {
        const sks = await skillsRes.json();
        if (Array.isArray(sks)) setSkillsList(sks);
      }
      if (kbRes.ok) {
        const kbs = await kbRes.json();
        if (Array.isArray(kbs)) setKbList(kbs);
      }
      if (toolsRes.ok) {
        const data = await toolsRes.json();
        if (Array.isArray(data?.tools)) setToolCatalog(data.tools);
      }
    } catch (e) {
      console.warn("Failed fetching tool registries:", e);
    }
  }, []);

  useEffect(() => {
    fetchAgents();
    loadRegistries();
  }, [fetchAgents, loadRegistries]);

  const formDataRef = useRef(formData);
  formDataRef.current = formData;
  const syncedAgentRef = useRef<AgentPersonaConfig | null>(null);

  useEffect(() => {
    const found = availableAgents.find((a) => a.id === selectedAgentId);
    const agentChanged = prevAgentIdRef.current !== selectedAgentId;
    const listReplaced = prevAgentsRef.current !== availableAgents;
    prevAgentIdRef.current = selectedAgentId;
    prevAgentsRef.current = availableAgents;
    if (!found || !(agentChanged || listReplaced)) return;
    // A refreshed list (the server's version of a built-in agent arrives after the page first
    // rendered the shipped one) replaces the form only while the person has not started editing;
    // otherwise a late response would silently wipe what they typed.
    const untouched =
      JSON.stringify(formDataRef.current) === JSON.stringify(syncedAgentRef.current);
    if (agentChanged || untouched) {
      syncedAgentRef.current = found;
      setFormData(found);
      setSaveError(null);
    }
  }, [selectedAgentId, availableAgents]);

  const userAgents = availableAgents.filter((a) => a.scope === "user" || a.user_id != null);
  const orgAgents = availableAgents.filter((a) => a.scope === "org" || (!a.scope && a.user_id == null));

  const isCurrentAgentOrg = formData.scope === "org" || (!formData.scope && formData.user_id == null);
  // A built-in agent is edited for the whole organization by an administrator; others save a copy.
  const editsBuiltInInPlace = !!formData.built_in && isAdmin;
  const canEditCurrentAgent = formData.built_in ? true : !isCurrentAgentOrg || isAdmin;

  const isAllowAll = formData.whitelisted_tools?.includes("*");

  // Every tool this agent could be given: the live catalog and what its MCP servers discovered.
  const allToolIds = [
    ...(toolCatalog.length ? toolCatalog.map((t) => t.name) : SYSTEM_TOOLS.map((t) => t.id)),
    ...mcpServers.flatMap((s) => (s.tools || []).map((t) => mcpToolName(s.name, t.name))),
  ];
  const allowedToolIds = isAllowAll ? allToolIds : (formData.whitelisted_tools || []).filter((t) => t !== "*");
  const { preview: loadingPreview, deferred: deferredTools, failed: loadingFailed } = useToolLoadingPreview({
    activeTools: formData.active_tools || [],
    toolNames: allowedToolIds,
  });
  const coreTools = new Set(loadingPreview?.core ?? []);
  const promptWarnings = usePersonaLint(formData.system_prompt || "");

  const handleToggleAllowAll = () => {
    if (!canEditCurrentAgent) return;
    if (isAllowAll) {
      setFormData({ ...formData, whitelisted_tools: ["bash_exec", "file_read", "file_write"] });
    } else {
      setFormData({ ...formData, whitelisted_tools: ["*"] });
    }
  };

  const togglePinned = (toolId: string) => {
    if (!canEditCurrentAgent) return;
    const pinned = formData.active_tools || [];
    setFormData({
      ...formData,
      active_tools: pinned.includes(toolId)
        ? pinned.filter((t) => t !== toolId)
        : [...pinned, toolId],
    });
  };

  const handleToolToggle = (toolId: string) => {
    if (!canEditCurrentAgent) return;
    const current = formData.whitelisted_tools || [];
    if (current.includes("*")) {
      const updated = allToolIds.filter((t) => t !== toolId);
      setFormData({ ...formData, whitelisted_tools: updated });
      return;
    }

    const updated = current.includes(toolId)
      ? current.filter((t) => t !== toolId)
      : [...current, toolId];
    setFormData({ ...formData, whitelisted_tools: updated });
  };

  const handleSkillToggle = (skillId: string) => {
    if (!canEditCurrentAgent) return;
    const current = formData.skill_ids || [];
    const updated = current.includes(skillId)
      ? current.filter((s) => s !== skillId)
      : [...current, skillId];
    setFormData({ ...formData, skill_ids: updated });
  };

  const handleKbToggle = (kbId: string) => {
    if (!canEditCurrentAgent) return;
    const current = formData.knowledge_base_ids || [];
    const updated = current.includes(kbId)
      ? current.filter((k) => k !== kbId)
      : [...current, kbId];
    setFormData({ ...formData, knowledge_base_ids: updated });
  };

  const handleSave = async () => {
    if (!canEditCurrentAgent) return;
    if (formData.cpu_limit && !/^\d+(\.\d+)?m?$/.test(formData.cpu_limit.trim())) {
      setSaveError("CPU limit must be a valid core count (e.g. '2' or '500m')");
      return;
    }
    if (formData.memory_limit && !/^\d+(\.\d+)?(Ki|Mi|Gi|Ti|K|M|G|T)?$/i.test(formData.memory_limit.trim())) {
      setSaveError("Memory limit must be a valid memory format (e.g. '4Gi' or '512Mi')");
      return;
    }
    setIsSaving(true);
    setSaveError(null);
    try {
      const res = await fetch("/v1/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Built-in personas are read-only; saving one stores an editable copy.
        body: JSON.stringify(
          formData.built_in && !editsBuiltInInPlace
            ? {
                ...formData,
                id: `persona-${Date.now().toString(36)}`,
                name: `${formData.name} (copy)`,
                built_in: false,
              }
            : formData
        ),
      });

      if (!res.ok) {
        let errorDetail = "";
        try {
          const errorJson = await res.json();
          errorDetail = errorJson.detail || errorJson.message || JSON.stringify(errorJson);
        } catch {
          errorDetail = await res.text();
        }
        throw new Error(errorDetail || "Failed to save persona");
      }

      const saved: AgentPersonaConfig = await res.json();
      const updatedList = availableAgents.map((a) => (a.id === saved.id ? saved : a));
      if (!updatedList.some((a) => a.id === saved.id)) {
        updatedList.push(saved);
      }
      setAvailableAgents(updatedList);
      if (activeAgent.id === saved.id || formData.built_in) {
        selectAgent(saved);
      }
      setIsSaved(true);
      setTimeout(() => setIsSaved(false), 2500);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error saving persona";
      setSaveError(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleResetBuiltIn = async () => {
    if (!confirm(`Restore '${formData.name}' to its shipped definition for the organization?`)) return;
    setSaveError(null);
    const res = await fetch(`/v1/agents/${formData.id}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setSaveError(body.detail || "Failed to reset the agent");
      return;
    }
    const listRes = await fetch("/v1/agents");
    if (!listRes.ok) return;
    const agents: AgentPersonaConfig[] = await listRes.json();
    setAvailableAgents(agents);
    const restored = agents.find((a) => a.id === formData.id);
    if (restored) {
      setFormData(restored);
      if (activeAgent.id === restored.id) selectAgent(restored);
    }
  };

  const handleCreateNew = (scope: "user" | "org") => {
    const newId = `persona-${Date.now().toString(36)}`;
    const newPersona: AgentPersonaConfig = {
      id: newId,
      name: scope === "user" ? "My Personal Agent" : "Team Specialist Agent",
      role_title: "Autonomous Specialist",
      description: "Custom agent persona with tailored system instructions and scoped permissions.",
      system_prompt:
        "You are an autonomous engineering agent specialized in mission-critical development. Verify all solutions with automated tests and follow clean code craftsmanship.",
      model: "",
      whitelisted_tools: ["bash_exec", "file_read", "file_write"],
      skill_ids: [],
      knowledge_base_ids: [],
      scope,
      icon: "bot",
    };
    setAvailableAgents([...availableAgents, newPersona]);
    setSelectedAgentId(newId);
    setFormData(newPersona);
  };

  const handleDeleteAgent = async (agent: AgentPersonaConfig, e: React.MouseEvent) => {
    e.stopPropagation();
    if (agent.built_in) {
      if (agent.enabled === false) {
        if (!confirm(`Re-enable built-in agent '${agent.name}' and reset to factory defaults?`)) return;
        try {
          const res = await fetch(`/v1/agents/${agent.id}`, { method: "DELETE" });
          if (res.ok) {
            const listRes = await fetch("/v1/agents");
            if (listRes.ok) {
              const updated = await listRes.json();
              setAvailableAgents(updated);
              const reenabled = updated.find((a: AgentPersonaConfig) => a.id === agent.id);
              if (reenabled && selectedAgentId === agent.id) setFormData(reenabled);
            }
          }
        } catch {
          setSaveError("Failed to reset agent persona");
        }
        return;
      }

      if (!confirm(`Disable built-in agent '${agent.name}'? It will be deactivated and hidden from cockpit missions.`)) return;
      try {
        const res = await fetch(`/v1/agents/${agent.id}?disable=true`, { method: "DELETE" });
        if (res.ok) {
          const updated = availableAgents.map((a) => (a.id === agent.id ? { ...a, enabled: false } : a));
          setAvailableAgents(updated);
          if (selectedAgentId === agent.id) {
            setFormData((prev) => ({ ...prev, enabled: false }));
          }
        }
      } catch {
        setSaveError("Failed to disable agent persona");
      }
      return;
    }

    if (!confirm(`Are you sure you want to delete agent '${agent.name}'?`)) return;
    try {
      const res = await fetch(`/v1/agents/${agent.id}`, { method: "DELETE" });
      if (res.ok) {
        const remaining = availableAgents.filter((a) => a.id !== agent.id);
        setAvailableAgents(remaining);
        if (selectedAgentId === agent.id) {
          setSelectedAgentId(remaining[0]?.id || "");
        }
      }
    } catch {
      setSaveError("Failed to delete agent persona");
    }
  };

  const renderAgentListItem = (agent: AgentPersonaConfig, canManage: boolean) => {
    const isSelected = agent.id === selectedAgentId;
    const isActiveInCockpit = activeAgent.id === agent.id;
    const isEnabled = agent.enabled !== false;

    return (
      <div
        key={agent.id}
        onClick={() => setSelectedAgentId(agent.id)}
        className={`p-3 rounded-lg border transition-all cursor-pointer ${
          isSelected
            ? "border-cyan bg-cyan/10 shadow-sm shadow-cyan/10"
            : !isEnabled
            ? "border-border/40 bg-surface-card/40 opacity-70 hover:opacity-100 hover:border-border"
            : "border-border/80 bg-surface-card hover:bg-surface hover:border-border"
        }`}
      >
        <div className="flex items-start gap-2.5">
          <div className="w-8 h-8 rounded-lg border border-border/70 bg-void flex items-center justify-center shrink-0">
            {getAgentIcon(agent.icon)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between">
              <span className={`font-semibold text-xs truncate ${!isEnabled ? "line-through text-muted-foreground" : "text-foreground"}`}>
                {agent.name}
              </span>
              <div className="flex items-center gap-1.5 shrink-0">
                {agent.built_in && (
                  <span className="text-[9px] text-neutral-700 dark:text-neutral-200 bg-surface px-1.5 py-0.5 rounded border border-border font-medium">
                    Built-in
                  </span>
                )}
                {isEnabled ? (
                  <span className="text-[9px] text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/30 font-medium">
                    Active
                  </span>
                ) : (
                  <span className="text-[9px] text-neutral-600 dark:text-neutral-300 bg-surface px-1.5 py-0.5 rounded border border-border">
                    Off
                  </span>
                )}
                {isActiveInCockpit && (
                  <span className="text-[9px] text-cyan bg-cyan/20 px-1.5 py-0.5 rounded-lg border border-cyan/30">
                    ACTIVE
                  </span>
                )}
                {canManage && (
                  <button
                    type="button"
                    onClick={(e) => handleDeleteAgent(agent, e)}
                    className="p-1 rounded-lg hover:bg-rose-950/40 text-neutral-500 hover:text-rose-400"
                    title={agent.built_in ? (isEnabled ? "Disable agent" : "Reset / Re-enable agent") : "Delete agent"}
                  >
                    {agent.built_in && !isEnabled ? (
                      <RotateCcw className="w-3 h-3 text-amber-400" />
                    ) : (
                      <Trash2 className="w-3 h-3" />
                    )}
                  </button>
                )}
              </div>
            </div>
            <div className="text-[10px] text-cyan/80 mt-0.5">
              {agent.role_title}
            </div>
            {agent.description && (
              <p className="text-[11px] text-muted-foreground line-clamp-2 mt-1">
                {agent.description}
              </p>
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      {/* Page Header */}
      <PageHeader
        title="Custom agent personas"
        icon={<Bot className="h-5 w-5" aria-hidden="true" />}
        description="Personal and organization agent personas with specialized skills, MCP tools, and RAG knowledge."
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Two-tier Personas catalog */}
        <div className="space-y-6">
          <UserScopeSection
            title="Personal Agents"
            description="Private custom agents you configured."
            action={
              <Button size="sm" variant="secondary" onClick={() => handleCreateNew("user")}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                New agent
              </Button>
            }
          >
            {userAgents.length > 0 ? (
              <div className="space-y-2">
                {userAgents.map((agent) => renderAgentListItem(agent, true))}
              </div>
            ) : (
              <div className="p-4 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
                No personal agent personas yet.
              </div>
            )}
          </UserScopeSection>

          <ScopeDivider label="Organization Scope" />

          <OrgScopeSection
            title="Organization Agents"
            description="Shared default agents available across the workspace."
            action={
              <Button size="sm" variant="secondary" onClick={() => handleCreateNew("org")}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                New org agent
              </Button>
            }
          >
            {orgAgents.length > 0 ? (
              <div className="space-y-2">
                {orgAgents.map((agent) => renderAgentListItem(agent, isAdmin))}
              </div>
            ) : (
              <div className="p-4 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
                No organization agents defined.
              </div>
            )}
          </OrgScopeSection>
        </div>

        {/* Right column: Edit Persona Form */}
        <div className="lg:col-span-2 rounded-lg border border-border/80 bg-surface-card p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/60 pb-3">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg border border-border/70 bg-void flex items-center justify-center">
                {getAgentIcon(formData.icon)}
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-bold text-foreground">
                    EDIT: {formData.name}
                  </h2>
                  <span className="px-1.5 py-0.2 rounded-lg text-[9px] bg-surface border border-border text-neutral-400">
                    {formData.scope || "org"}
                  </span>
                </div>
                <span className="text-[10px] text-muted-foreground">ID: {formData.id}</span>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={!canEditCurrentAgent}
                onClick={() => setFormData({ ...formData, enabled: formData.enabled === false ? true : false })}
                className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border transition-colors ${
                  formData.enabled !== false
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-500/20"
                    : "border-border bg-surface text-neutral-600 dark:text-neutral-300 hover:text-foreground"
                }`}
                title={formData.enabled !== false ? "Click to deactivate agent" : "Click to activate agent"}
              >
                <span className={`w-2 h-2 rounded-full ${formData.enabled !== false ? "bg-emerald-500" : "bg-neutral-400"}`} />
                <span>{formData.enabled !== false ? "Active" : "Disabled"}</span>
              </button>

              <button
                type="button"
                onClick={() => selectAgent(formData)}
                disabled={formData.enabled === false}
                className="px-3 py-1.5 rounded-lg border border-border hover:border-cyan/50 bg-surface text-xs text-muted-foreground hover:text-cyan transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                title={formData.enabled === false ? "Enable this agent to activate in cockpit" : "Activate this agent in the Cockpit"}
              >
                Set Active
              </button>
              {editsBuiltInInPlace && (formData.customized || formData.enabled === false) && (
                <button
                  type="button"
                  onClick={handleResetBuiltIn}
                  className="px-3 py-1.5 rounded-lg border border-border hover:border-rose-500/50 bg-surface text-xs text-muted-foreground hover:text-rose-400 transition-colors"
                >
                  Reset to default
                </button>
              )}
              <button
                type="button"
                onClick={handleSave}
                disabled={isSaving}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-cyan hover:bg-cyan/90 text-slate-950 font-bold text-xs transition-all shadow-sm shadow-cyan/20 cursor-pointer disabled:opacity-50"
              >
                {isSaved ? (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>SAVED!</span>
                  </>
                ) : (
                  <>
                    <Save className="w-3.5 h-3.5" />
                    <span>{isSaving ? "Saving…" : "Save persona"}</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Error Banner */}
          {saveError && (
            <div data-testid="persona-save-error" className="p-3 bg-rose-950/60 border border-rose-500/50 rounded-lg text-xs text-rose-300 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
              <span>{saveError}</span>
            </div>
          )}

          {/* Form Fields */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="AGENT DISPLAY NAME">
              {(control) => (
                <Input {...control}
                  disabled={!canEditCurrentAgent}
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                />
              )}
            </Field>

            <Field label="ROLE TITLE">
              {(control) => (
                <Input {...control}
                  disabled={!canEditCurrentAgent}
                  value={formData.role_title}
                  onChange={(e) => setFormData({ ...formData, role_title: e.target.value })}
                />
              )}
            </Field>
          </div>

          <Field label="DESCRIPTION">
            {(control) => (
              <Input {...control}
                disabled={!canEditCurrentAgent}
                value={formData.description || ""}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Brief description of this agent's specialization..."
              />
            )}
          </Field>

          <Field
            label="Model"
            hint="Empty uses the member's preferred model, else the organization's default."
          >
            {(control) => (
              <ModelPicker
                {...control}
                data-testid="agent-model"
                disabled={!canEditCurrentAgent}
                value={formData.model || ""}
                onChange={(model) => setFormData({ ...formData, model })}
                inheritLabel="Organization default"
              />
            )}
          </Field>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Field
              label="Reasoning effort"
              hint="For reasoning models. A model that does not take the level gets the nearest one it does."
            >
              {(control) => (
                <Select
                  {...control}
                  data-testid="agent-reasoning-effort"
                  disabled={!canEditCurrentAgent}
                  value={formData.reasoning_effort ?? ""}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      reasoning_effort: (e.target.value || undefined) as AgentPersonaConfig["reasoning_effort"],
                    })
                  }
                  className="w-full"
                >
                  <option value="">Model / organization default</option>
                  <option value="none">Off (no reasoning, fastest)</option>
                  <option value="minimal">Minimal</option>
                  <option value="low">Low (faster, budget-friendly)</option>
                  <option value="medium">Medium (balanced reasoning)</option>
                  <option value="high">High (deep architectural thinking)</option>
                  <option value="xhigh">Extra high</option>
                  <option value="max">Max</option>
                </Select>
              )}
            </Field>

            <Field
              label="Thinking token budget"
              hint="Tokens for extended thinking (Claude 3.7 Sonnet, Gemini 2.5). Empty uses default."
            >
              {(control) => (
                <Input
                  {...control}
                  data-testid="agent-thinking-budget"
                  type="number"
                  min={0}
                  step={1024}
                  disabled={!canEditCurrentAgent}
                  value={formData.thinking_budget_tokens ?? ""}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      thinking_budget_tokens: e.target.value === "" ? undefined : Number(e.target.value),
                    })
                  }
                  placeholder="e.g. 8192"
                  className="w-full"
                />
              )}
            </Field>

            <Field
              label="Sampling temperature"
              hint="0.0 is deterministic and focused; 1.0+ is creative. Empty uses model default."
            >
              {(control) => (
                <Input
                  {...control}
                  data-testid="agent-temperature"
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  disabled={!canEditCurrentAgent}
                  value={formData.temperature ?? ""}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      temperature: e.target.value === "" ? undefined : Number(e.target.value),
                    })
                  }
                  placeholder="Model default"
                  className="w-full"
                />
              )}
            </Field>
          </div>

          {/* The sandbox type this agent's sessions start in, unless their creator picks one */}
          <Field
            label="Sandbox"
            hint="Where this agent's new sessions run when the person starting them picks no sandbox. A sandbox picked in the composer still wins."
          >
            {(control) => (
              <Select {...control}
                data-testid="agent-sandbox"
                disabled={!canEditCurrentAgent}
                value={formData.sandbox_id || ""}
                onChange={(e) => setFormData({ ...formData, sandbox_id: e.target.value })}
                className="max-w-xs"
              >
                <option value="">Organization default</option>
                {sandboxTypes.map((box) => (
                  <option key={box.id} value={box.id}>
                    {box.name}
                    {box.is_default ? " (organization default)" : ""}
                  </option>
                ))}
                {formData.sandbox_id && !sandboxTypes.some((box) => box.id === formData.sandbox_id) && (
                  <option value={formData.sandbox_id}>Removed sandbox type (uses the default)</option>
                )}
              </Select>
            )}
          </Field>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field
              label="CPU limit"
              hint="Dedicated CPU cores (e.g. '2' or '4'). Empty inherits sandbox profile default."
            >
              {(control) => (
                <Input
                  {...control}
                  data-testid="agent-cpu-limit"
                  disabled={!canEditCurrentAgent}
                  value={formData.cpu_limit || ""}
                  onChange={(e) => setFormData({ ...formData, cpu_limit: e.target.value })}
                  placeholder="Profile default (e.g. 2)"
                  className="w-full"
                />
              )}
            </Field>

            <Field
              label="Memory limit"
              hint="RAM limit (e.g. '4Gi' or '8Gi'). Empty inherits sandbox profile default."
            >
              {(control) => (
                <Input
                  {...control}
                  data-testid="agent-memory-limit"
                  disabled={!canEditCurrentAgent}
                  value={formData.memory_limit || ""}
                  onChange={(e) => setFormData({ ...formData, memory_limit: e.target.value })}
                  placeholder="Profile default (e.g. 4Gi)"
                  className="w-full"
                />
              )}
            </Field>
          </div>

          {/* Step limit: empty inherits the organization's, which is also the ceiling */}
          <Field
            label="Max steps per turn"
            hint={
              orgMaxTurns === 0
                ? "Leave empty for no limit (the organization sets none); 0 also means no limit."
                : orgMaxTurns !== null
                  ? `Leave empty to use the organization's limit (${orgMaxTurns}); at most ${orgMaxTurns}.`
                  : "Leave empty to use the organization's limit."
            }
          >
            {(control) => (
              <Input {...control}
                type="number"
                min={orgMaxTurns === 0 ? 0 : 1}
                max={orgMaxTurns ? orgMaxTurns : undefined}
                disabled={!canEditCurrentAgent}
                value={formData.max_turns ?? ""}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    max_turns: e.target.value === "" ? null : Number(e.target.value),
                  })
                }
                placeholder={
                  orgMaxTurns === 0
                    ? "No limit"
                    : orgMaxTurns !== null
                      ? String(orgMaxTurns)
                      : "Organization limit"
                }
                className="max-w-[10rem]"
              />
            )}
          </Field>

          {/* Default Permission Mode */}
          <Field
            label="Default permission mode"
            hint="The supervision level for sessions using this agent: Plan (read-only), Ask (approve all), Auto-edit (free edits, approve commands/git), Auto (balanced), Bypass (unsupervised). Empty inherits the user or organization default."
          >
            {(control) => (
              <Select {...control}
                data-testid="agent-permission-mode"
                disabled={!canEditCurrentAgent}
                value={formData.default_permission_mode ?? ""}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    default_permission_mode: (e.target.value || null) as AgentPersonaConfig["default_permission_mode"],
                  })
                }
                className="max-w-xs"
              >
                <option value="" title="Supervision follows user or organization preference">
                  Inherit user / organization default
                </option>
                <option value="plan" title="Read-only: The agent inspects code and creates plans without modifying files or running commands.">
                  Plan (read-only)
                </option>
                <option value="ask" title="Strict: Every file edit, shell command, and git publish requires approval.">
                  Ask (approve every action)
                </option>
                <option value="auto_edit" title="Semi-autonomous: Edits run freely; commands and git publications require approval.">
                  Auto-edit (edits run freely; approve commands & git)
                </option>
                <option value="auto" title="Balanced: Edits and standard commands run freely; only critical actions ask.">
                  Auto (balanced; ask on critical actions)
                </option>
                <option value="bypass" title="Full autonomy: Zero approval prompts for any tool (requires Allow bypass permissions in Admin Governance).">
                  Bypass (unsupervised / no approvals)
                </option>
              </Select>
            )}
          </Field>

          <ToolLoadSummary preview={loadingPreview} failed={loadingFailed} />

          <Field
            label="Kept active"
            hint="Every agent gets the core tools with every step; the other tools it may use are listed by name and loaded when it needs them. Tools named here, or matching a pattern such as mcp__signoz*__*search* (one per line), are sent with every step too. Use the pin on an allowed tool below."
          >
            {(control) => (
              <Textarea {...control}
                data-testid="agent-active-tools"
                disabled={!canEditCurrentAgent}
                rows={3}
                value={(formData.active_tools || []).join("\n")}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    active_tools: e.target.value
                      .split("\n")
                      .map((line) => line.trim())
                      .filter(Boolean),
                  })
                }
                className="font-mono text-xs"
              />
            )}
          </Field>

          {/* System Prompt */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] text-muted-foreground font-semibold">
                SYSTEM PROMPT & INSTRUCTION MATRIX
              </label>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPreviewMarkdown(false)}
                  className={`px-2 py-0.5 rounded-lg text-[10px] flex items-center gap-1 ${
                    !previewMarkdown
                      ? "bg-cyan/20 text-cyan border border-cyan/30"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Edit3 className="w-3 h-3" /> Edit
                </button>
                <button
                  type="button"
                  onClick={() => setPreviewMarkdown(true)}
                  className={`px-2 py-0.5 rounded-lg text-[10px] flex items-center gap-1 ${
                    previewMarkdown
                      ? "bg-cyan/20 text-cyan border border-cyan/30"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Eye className="w-3 h-3" /> Preview Markdown
                </button>
              </div>
            </div>

            {previewMarkdown ? (
              <div data-testid="markdown-preview-box" className="w-full min-h-[140px] p-3 rounded-lg border border-border bg-void/80 max-h-60 overflow-y-auto">
                <MarkdownRenderer content={formData.system_prompt} />
              </div>
            ) : (
              <textarea
                rows={5}
                disabled={!canEditCurrentAgent}
                value={formData.system_prompt}
                onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                className="w-full px-3 py-2 rounded-lg border border-border bg-void text-xs font-mono text-foreground/90 focus:outline-none focus:border-cyan leading-relaxed disabled:opacity-60"
                placeholder="Enter markdown-formatted system instructions..."
              />
            )}
            {promptWarnings.length > 0 && (
              <ul data-testid="persona-warnings" role="status" className="mt-2 space-y-1">
                {promptWarnings.map((warning) => (
                  <li
                    key={warning.message}
                    data-testid={`persona-warning-${warning.kind}`}
                    className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300"
                  >
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    {warning.message}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* ATTACHED SKILLS (Prompt Guidelines) */}
          <div className="space-y-2 pt-2 border-t border-border/60">
            <div className="flex items-center gap-2">
              <Code2 className="w-4 h-4 text-brand" />
              <label className="text-xs text-foreground font-semibold">
                CUSTOM SKILLS & PYTHON EXTENSIONS ({skillsList.length} AVAILABLE)
              </label>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {skillsList.map((skill) => {
                const isAttached = formData.skill_ids?.includes(skill.id);
                return (
                  <div
                    key={skill.id}
                    onClick={() => handleSkillToggle(skill.id)}
                    className={`flex items-start gap-2.5 p-2.5 rounded-lg border transition-colors cursor-pointer ${
                      isAttached
                        ? "border-brand/40 bg-brand/5 text-foreground"
                        : "border-border/60 bg-surface/30 text-muted-foreground hover:border-border"
                    }`}
                  >
                    <div
                      className={`w-4 h-4 rounded-lg border flex items-center justify-center shrink-0 mt-0.5 ${
                        isAttached ? "border-brand bg-brand text-slate-950 font-bold" : "border-border"
                      }`}
                    >
                      {isAttached && <Check className="w-3 h-3" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-medium text-foreground truncate">
                        {skill.title || skill.name}
                      </div>
                      {skill.description && (
                        <div className="text-[10px] text-muted-foreground truncate mt-0.5">
                          {skill.description}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* ATTACHED KNOWLEDGE BASES (PGVector) */}
          <div className="space-y-2 pt-2 border-t border-border/60">
            <div className="flex items-center gap-2">
              <Database className="w-4 h-4 text-cyan" />
              <label className="text-xs text-foreground font-semibold">
                KNOWLEDGE BASES (PGVECTOR RAG) ({kbList.length} AVAILABLE)
              </label>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {kbList.map((kb) => {
                const isAttached = formData.knowledge_base_ids?.includes(kb.id);
                return (
                  <div
                    key={kb.id}
                    onClick={() => handleKbToggle(kb.id)}
                    className={`flex items-start gap-2.5 p-2.5 rounded-lg border transition-colors cursor-pointer ${
                      isAttached
                        ? "border-cyan/40 bg-cyan/5 text-foreground"
                        : "border-border/60 bg-surface/30 text-muted-foreground hover:border-border"
                    }`}
                  >
                    <div
                      className={`w-4 h-4 rounded-lg border flex items-center justify-center shrink-0 mt-0.5 ${
                        isAttached ? "border-cyan bg-cyan text-slate-950 font-bold" : "border-border"
                      }`}
                    >
                      {isAttached && <Check className="w-3 h-3" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-medium text-foreground truncate">
                        {kb.name}
                      </div>
                      <div className="text-[10px] text-muted-foreground truncate mt-0.5">
                        {kb.documents_count || 0} documents indexed
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* TOOL WHITELISTING: System Tools & Real MCP Servers */}
          <div className="space-y-4 pt-2 border-t border-border/60">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2">
              <div>
                <label className="block text-xs text-foreground font-semibold">
                  AUTHORIZED TOOLS & MCP SERVERS
                </label>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Enable tools from sandbox execution and connected MCP servers.
                </p>
              </div>

              <button
                type="button"
                onClick={handleToggleAllowAll}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs transition-all cursor-pointer ${
                  isAllowAll
                    ? "bg-blue-600/20 border-blue-500 text-blue-400 font-semibold"
                    : "bg-surface hover:bg-overlay border-border text-muted-foreground"
                }`}
              >
                <div
                  className={`w-3.5 h-3.5 rounded-lg border flex items-center justify-center ${
                    isAllowAll ? "bg-blue-600 border-blue-500 text-white" : "border-border"
                  }`}
                >
                  {isAllowAll && <Check className="w-2.5 h-2.5" />}
                </div>
                <span>Allow all tools</span>
              </button>
            </div>

            {isAllowAll && (
              <div className="p-3 rounded-lg border border-blue-500/30 bg-blue-600/10 text-xs text-blue-300 flex items-center gap-2">
                <Check className="w-4 h-4 text-blue-400 shrink-0" />
                <span>
                  <strong>Full Access Granted:</strong> This agent is authorized with wildcard permissions (<code>*</code>) across all system sandbox tools, connected MCP servers, skills, and knowledge bases.
                </span>
              </div>
            )}

            {/* Authorized tools — driven by the live backend catalog */}
            <div className="space-y-3">
              <span className="text-[11px] font-semibold text-foreground/90">
                AUTHORIZED TOOLS ({toolCatalog.length || SYSTEM_TOOLS.length})
              </span>
              {Object.entries(
                (toolCatalog.length
                  ? toolCatalog
                  : SYSTEM_TOOLS.map((tool) => ({
                      name: tool.id,
                      description: tool.description,
                      category: "Sandbox & Repository",
                      toolset: null,
                    }))
                ).reduce<
                  Record<
                    string,
                    { name: string; description: string; category: string; toolset: string | null }[]
                  >
                >((acc, tool) => {
                  (acc[tool.category] ||= []).push(tool);
                  return acc;
                }, {})
              ).map(([category, tools]) => (
                <div key={category} className="space-y-2">
                  <span className="text-[10px] text-muted-foreground">
                    {category}
                  </span>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {tools.map((tool) => {
                      const isChecked =
                        isAllowAll || formData.whitelisted_tools?.includes(tool.name);
                      return (
                        <div
                          key={tool.name}
                          data-testid={`agent-tool-${tool.name}`}
                          onClick={() => handleToolToggle(tool.name)}
                          className={`flex items-start gap-2.5 p-2.5 rounded-lg border transition-colors cursor-pointer ${
                            isChecked
                              ? "border-blue-500/40 bg-blue-500/5 text-foreground"
                              : "border-border/60 bg-surface/30 text-muted-foreground hover:border-border"
                          }`}
                        >
                          <div
                            className={`w-4 h-4 rounded-lg border flex items-center justify-center shrink-0 mt-0.5 ${
                              isChecked ? "border-blue-500 bg-blue-600 text-white" : "border-border"
                            }`}
                          >
                            {isChecked && <Check className="w-3 h-3" />}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center justify-between gap-1">
                              <span className="text-xs font-medium text-foreground truncate">
                                {tool.name}
                              </span>
                              {tool.toolset && (
                                <span className="text-[9px] px-1 rounded-lg bg-surface border border-border text-neutral-400 shrink-0">
                                  {tool.toolset}
                                </span>
                              )}
                              {isChecked && loadingPreview && (
                                <ToolLoadBadge deferred={deferredTools.has(tool.name)} core={coreTools.has(tool.name)} />
                              )}
                              {/* A core tool is sent anyway: only the others can be kept active. */}
                              {isChecked && loadingPreview && !coreTools.has(tool.name) && (
                                <PinToggle
                                  tool={tool.name}
                                  pinned={(formData.active_tools || []).includes(tool.name)}
                                  disabled={!canEditCurrentAgent}
                                  onToggle={togglePinned}
                                />
                              )}
                            </div>
                            <div className="text-[10px] text-muted-foreground leading-tight mt-0.5">
                              {tool.description}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>

            {/* Connected MCP Servers */}
            {mcpServers.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Server className="w-3.5 h-3.5 text-cyan" />
                  <span className="text-[11px] font-semibold text-foreground/90">
                    MCP SERVERS (MODEL CONTEXT PROTOCOL) ({mcpServers.length} Servers)
                  </span>
                </div>
                <div className="space-y-3">
                  {mcpServers.map((server) => {
                    const serverToolNames = (server.tools || []).map((t) => t.name);

                    return (
                      <div
                        key={server.id}
                        className="p-3 rounded-lg border border-border/80 bg-surface/40 space-y-2"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-semibold text-foreground">
                              {server.name}
                            </span>
                            <span className="px-1.5 py-0.2 rounded-lg text-[9px] bg-surface border border-border text-neutral-400">
                              {server.scope || "org"}
                            </span>
                          </div>
                          <span className="text-[10px] text-muted-foreground">
                            {serverToolNames.length} tools
                            {loadingPreview && (() => {
                              const allowedHere = serverToolNames
                                .map((t) => mcpToolName(server.name, t))
                                .filter((id) => allowedToolIds.includes(id));
                              const waiting = allowedHere.filter((id) => deferredTools.has(id)).length;
                              return allowedHere.length
                                ? ` · ${allowedHere.length - waiting} active, ${waiting} deferred`
                                : "";
                            })()}
                          </span>
                        </div>

                        {serverToolNames.length > 0 ? (
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            {serverToolNames.map((toolName) => {
                              const toolId = mcpToolName(server.name, toolName);
                              const isChecked = isAllowAll || formData.whitelisted_tools?.includes(toolId);
                              return (
                                <div
                                  key={toolId}
                                  onClick={() => handleToolToggle(toolId)}
                                  className={`flex items-start gap-2 p-2 rounded-lg border transition-colors cursor-pointer ${
                                    isChecked
                                      ? "border-cyan/40 bg-cyan/5 text-foreground"
                                      : "border-border/60 bg-surface/30 text-muted-foreground hover:border-border"
                                  }`}
                                >
                                  <div
                                    className={`w-3.5 h-3.5 rounded-lg border flex items-center justify-center shrink-0 mt-0.5 ${
                                      isChecked ? "border-cyan bg-cyan text-slate-950 font-bold" : "border-border"
                                    }`}
                                  >
                                    {isChecked && <Check className="w-2.5 h-2.5" />}
                                  </div>
                                  <span className="text-xs truncate flex-1">{toolName}</span>
                                  {isChecked && loadingPreview && (
                                    <ToolLoadBadge deferred={deferredTools.has(toolId)} />
                                  )}
                                  {isChecked && loadingPreview && (
                                    <PinToggle
                                      tool={toolId}
                                      pinned={(formData.active_tools || []).includes(toolId)}
                                      disabled={!canEditCurrentAgent}
                                      onToggle={togglePinned}
                                    />
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div className="text-[11px] text-neutral-500 dark:text-neutral-400 italic">
                            No tools discovered for this server. Probe server from MCP settings.
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** The name an MCP server's tool has inside a turn: ``mcp__<server slug>__<tool>`` (64 chars at
 * most), built exactly as the API does, so the agent's tool list matches what it runs with. */
function mcpToolName(serverName: string, tool: string): string {
  const slug =
    serverName
      .replace(/[^a-zA-Z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .toLowerCase() || "server";
  return `mcp__${slug}__${tool.replace(/[^a-zA-Z0-9_-]/g, "_")}`.slice(0, 64);
}

/** Keeps an allowed tool loaded on every step (otherwise it may be loaded on demand). */
function PinToggle({
  tool,
  pinned,
  disabled,
  onToggle,
}: {
  tool: string;
  pinned: boolean;
  disabled: boolean;
  onToggle: (tool: string) => void;
}) {
  return (
    <button
      type="button"
      data-testid={`agent-pin-${tool}`}
      aria-label={pinned ? `Stop keeping ${tool} active` : `Keep ${tool} active`}
      aria-pressed={pinned}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onToggle(tool);
      }}
      className={`p-0.5 rounded-lg shrink-0 cursor-pointer ${
        pinned ? "text-amber-400" : "text-neutral-500 hover:text-neutral-300"
      }`}
    >
      <Pin className={`w-3 h-3 ${pinned ? "fill-current" : ""}`} />
    </button>
  );
}
