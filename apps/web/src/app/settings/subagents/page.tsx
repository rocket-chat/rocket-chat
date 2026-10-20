"use client";

import { Field, Input } from "../../../components/ui/Field";
import { ModelPicker } from "../../../components/ui/ModelPicker";
import React, { useState, useEffect } from "react";
import { SubagentConfig } from "../../../types/mission";
import {
  Cpu,
  Shield,
  CheckCircle2,
  Search,
  Save,
  Check,
  Lock,
  Plus,
  Trash2,
  RotateCcw,
  Wrench,
  X,
  Sparkles,
} from "lucide-react";

// Shown until the organization's roles load; the same as the platform's built-in roles.
const READ_ONLY_TOOLS = [
  "file_read",
  "grep_search",
  "glob_files",
  "list_dir",
  "code_search",
  "web_search",
  "web_fetch",
];

const DEFAULT_SUBAGENTS: SubagentConfig[] = [
  {
    id: "security_auditor",
    name: "Security & Penetration Auditor",
    role_title: "Application Security Specialist",
    description: "Audits source code for injection attacks, RLS boundary leaks, secret exposure, and permissions.",
    system_prompt:
      "You are a dedicated Security Auditor subagent. Inspect source files and configuration for vulnerabilities (OWASP Top 10, SQL injection, RLS bypasses, secret leakage). Synthesize your findings concisely with specific line references and suggested remediations.",
    model: null,
    max_turns: 30,
    whitelisted_tools: READ_ONLY_TOOLS,
    temperature: 0.2,
    enabled: true,
    built_in: true,
    customized: false,
  },
  {
    id: "qa_verifier",
    name: "QA & Test Verifier",
    role_title: "Automated Test Specialist",
    description: "Executes test suites in the sandbox, synthesizes failing cases, and verifies regressions.",
    system_prompt:
      "You are a QA & Test Verifier subagent. Run the tests (run_tests, or bash_exec for anything it does not cover), inspect the failures in the code, and report exactly which tests passed and which failed, with the assertion and the likely cause.",
    model: null,
    max_turns: 30,
    whitelisted_tools: ["run_tests", "bash_exec", "file_read", "grep_search", "list_dir"],
    temperature: 0.2,
    enabled: true,
    built_in: true,
    customized: false,
  },
  {
    id: "researcher",
    name: "Codebase & Web Researcher",
    role_title: "Technical Exploration Specialist",
    description: "Explores external documentation, packages, or codebases to answer architectural questions.",
    system_prompt:
      "You are a Codebase & Web Researcher subagent. Search technical documentation and read files to answer specific architectural or implementation questions without altering files.",
    model: null,
    max_turns: 30,
    whitelisted_tools: READ_ONLY_TOOLS,
    temperature: 0.2,
    enabled: true,
    built_in: true,
    customized: false,
  },
];

const FALLBACK_AVAILABLE_TOOLS = [
  { id: "file_read", name: "File Reader", desc: "Read file slices and analyze syntax" },
  { id: "grep_search", name: "Content Search", desc: "Search file contents with a pattern" },
  { id: "glob_files", name: "File Finder", desc: "Find files by name pattern" },
  { id: "list_dir", name: "Directory Listing", desc: "List a directory's contents" },
  { id: "code_search", name: "Symbol Search", desc: "Find where functions and types are defined" },
  { id: "file_write", name: "File Writer", desc: "Write files and patch content" },
  { id: "file_edit", name: "File Editor", desc: "Replace a block in an existing file" },
  { id: "apply_patch", name: "Patch Applicator", desc: "Apply unified diff patches cleanly" },
  { id: "bash_exec", name: "Bash Terminal Exec", desc: "Execute commands in isolated sandbox" },
  { id: "run_tests", name: "Test Runner", desc: "Run the repository's test suite" },
  { id: "web_search", name: "Live Web Search", desc: "Query internet documentation" },
  { id: "web_fetch", name: "URL Content Ingest", desc: "Download and convert documentation URLs" },
  { id: "workspace_status", name: "Workspace Status", desc: "Examine multi-repository workspace status" },
  { id: "git_status", name: "Git Status Check", desc: "Inspect git repository status" },
];

interface ToolItem {
  id: string;
  name: string;
  desc: string;
  category?: string;
}

export default function SubagentsSettingsPage() {
  const [subagents, setSubagents] = useState<SubagentConfig[]>(DEFAULT_SUBAGENTS);
  const [selectedId, setSelectedId] = useState<string>("security_auditor");
  const [formData, setFormData] = useState<SubagentConfig>(DEFAULT_SUBAGENTS[0]);
  const [runtimeTools, setRuntimeTools] = useState<ToolItem[]>(FALLBACK_AVAILABLE_TOOLS);
  const [toolSearch, setToolSearch] = useState<string>("");
  const [isSaved, setIsSaved] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [showNewModal, setShowNewModal] = useState<boolean>(false);
  const [newSubagentId, setNewSubagentId] = useState<string>("");
  const [newSubagentError, setNewSubagentError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/subagents")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setSubagents(data);
          setSelectedId((currentId) => {
            const matched = data.find((s) => s.id === currentId) || data[0];
            if (matched) {
              setFormData(matched);
              return matched.id;
            }
            return currentId;
          });
        }
      })
      .catch(() => {});

    fetch("/v1/tools")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          const mapped: ToolItem[] = data.map((t: { name: string; description?: string; category?: string }) => ({
            id: t.name,
            name: t.name,
            desc: t.description || "",
            category: t.category,
          }));
          setRuntimeTools(mapped);
        }
      })
      .catch(() => {});
  }, []);

  const handleSelectSubagent = (sub: SubagentConfig) => {
    setSelectedId(sub.id);
    setFormData({ ...sub });
    setIsSaved(false);
  };

  const isAllTools = formData.whitelisted_tools.includes("*");

  const toggleAllTools = () => {
    if (isAllTools) {
      setFormData({ ...formData, whitelisted_tools: READ_ONLY_TOOLS });
    } else {
      setFormData({ ...formData, whitelisted_tools: ["*"] });
    }
  };

  const toggleTool = (toolId: string) => {
    if (isAllTools) {
      // Transition from wildcard to specific list minus this tool
      const next = runtimeTools.map((t) => t.id).filter((id) => id !== toolId);
      setFormData({ ...formData, whitelisted_tools: next });
      return;
    }
    const current = formData.whitelisted_tools;
    const next = current.includes(toolId)
      ? current.filter((t) => t !== toolId)
      : [...current, toolId];
    setFormData({ ...formData, whitelisted_tools: next });
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await fetch(`/v1/subagents/${formData.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });
      if (res.ok) {
        const updated = await res.json();
        setSubagents((prev) =>
          prev.map((s) => (s.id === updated.id ? updated : s))
        );
        setFormData(updated);
        setIsSaved(true);
        setTimeout(() => setIsSaved(false), 2500);
      }
    } catch {
      setSubagents((prev) =>
        prev.map((s) => (s.id === formData.id ? formData : s))
      );
      setIsSaved(true);
      setTimeout(() => setIsSaved(false), 2500);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteOrReset = async () => {
    const isBuiltin = formData.built_in;
    const isEnabled = formData.enabled;

    if (isBuiltin) {
      if (isEnabled) {
        if (!window.confirm(`Disable "${formData.name}"? It will not be available for lead agent delegation.`)) return;
        setIsDeleting(true);
        try {
          const res = await fetch(`/v1/subagents/${formData.id}?disable=true`, { method: "DELETE" });
          if (res.ok) {
            const listRes = await fetch("/v1/subagents");
            if (listRes.ok) {
              const data: SubagentConfig[] = await listRes.json();
              setSubagents(data);
              const next = data.find((s) => s.id === formData.id) || data[0];
              if (next) setFormData(next);
            }
          }
        } finally {
          setIsDeleting(false);
        }
        return;
      }

      if (!window.confirm(`Reset "${formData.name}" to platform default settings (re-enabling it)?`)) return;
      setIsDeleting(true);
      try {
        const res = await fetch(`/v1/subagents/${formData.id}`, { method: "DELETE" });
        if (res.ok) {
          const listRes = await fetch("/v1/subagents");
          if (listRes.ok) {
            const data: SubagentConfig[] = await listRes.json();
            setSubagents(data);
            const next = data.find((s) => s.id === formData.id) || data[0];
            if (next) setFormData(next);
          }
        }
      } finally {
        setIsDeleting(false);
      }
      return;
    }

    if (!window.confirm(`Delete specialist "${formData.name}"? This action cannot be undone.`)) return;

    setIsDeleting(true);
    try {
      const res = await fetch(`/v1/subagents/${formData.id}`, {
        method: "DELETE",
      });
      if (res.ok) {
        const listRes = await fetch("/v1/subagents");
        if (listRes.ok) {
          const data: SubagentConfig[] = await listRes.json();
          setSubagents(data);
          const next = data.find((s) => s.id === formData.id) || data[0];
          if (next) {
            setSelectedId(next.id);
            setFormData(next);
          }
        }
      }
    } catch {
      // offline error fallback
    } finally {
      setIsDeleting(false);
    }
  };

  const handleCreateSpecialist = async () => {
    const id = newSubagentId.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(id)) {
      setNewSubagentError("ID must be 1-32 lowercase letters, numbers, '_' or '-'.");
      return;
    }
    if (subagents.some((s) => s.id === id)) {
      setNewSubagentError("A specialist with this ID already exists.");
      return;
    }

    const defaultName =
      id
        .replace(/[_-]/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase()) + " Specialist";

    const newSpecialist: SubagentConfig = {
      id,
      name: defaultName,
      role_title: "Specialist Delegate",
      description: "Dedicated subagent for scoped investigations and tasks.",
      system_prompt: `You are a specialized ${id} subagent. Complete the requested task thoroughly, synthesize findings, and report results concisely.`,
      model: null,
      max_turns: 30,
      whitelisted_tools: ["*"],
      temperature: 0.2,
      enabled: true,
      built_in: false,
      customized: false,
    };

    try {
      const res = await fetch(`/v1/subagents/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newSpecialist),
      });
      if (res.ok) {
        const saved = await res.json();
        setSubagents((prev) => [...prev, saved]);
        setSelectedId(saved.id);
        setFormData(saved);
        setShowNewModal(false);
        setNewSubagentId("");
        setNewSubagentError(null);
      } else {
        const err = await res.json().catch(() => ({}));
        setNewSubagentError(err.detail || "Failed to create specialist.");
      }
    } catch {
      setSubagents((prev) => [...prev, newSpecialist]);
      setSelectedId(newSpecialist.id);
      setFormData(newSpecialist);
      setShowNewModal(false);
      setNewSubagentId("");
      setNewSubagentError(null);
    }
  };

  const getSubagentIcon = (id: string) => {
    if (id.includes("security")) return <Shield className="w-4 h-4 text-amber-400" />;
    if (id.includes("qa") || id.includes("test")) return <CheckCircle2 className="w-4 h-4 text-emerald-400" />;
    if (id.includes("research")) return <Search className="w-4 h-4 text-sky-400" />;
    return <Cpu className="w-4 h-4 text-brand" />;
  };

  const filteredTools = runtimeTools.filter(
    (t) =>
      !toolSearch ||
      t.name.toLowerCase().includes(toolSearch.toLowerCase()) ||
      t.desc.toLowerCase().includes(toolSearch.toLowerCase())
  );

  return (
    <div className="max-w-6xl mx-auto px-6 py-8 space-y-8 font-sans">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-surface-border pb-6">
        <div>
          <div className="flex items-center gap-2 text-xs text-brand mb-1">
            <Cpu className="w-3.5 h-3.5" />
            <span>Autonomous Specialist Subagents</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            Subagents & Task Delegation
          </h1>
          <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-1">
            Configure autonomous specialists that execute focused, bounded investigation tasks inside the session container.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setNewSubagentId("");
              setNewSubagentError(null);
              setShowNewModal(true);
            }}
            data-testid="add-subagent-button"
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-surface-elevated hover:bg-surface-elevated/80 border border-surface-border text-foreground transition-all shadow-sm"
          >
            <Plus className="w-4 h-4 text-brand" />
            <span>Add Specialist</span>
          </button>

          <button
            onClick={handleSave}
            disabled={isSaving}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-sm ${
              isSaved
                ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/40"
                : "bg-brand hover:bg-brand/90 text-white border border-brand/50"
            }`}
          >
            {isSaved ? (
              <>
                <Check className="w-4 h-4" />
                <span>Saved Settings</span>
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                <span>{isSaving ? "Saving..." : "Save Configuration"}</span>
              </>
            )}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Left: Subagents Navigation List */}
        <div className="lg:col-span-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 block">
              Specialist Fleet ({subagents.length})
            </span>
          </div>

          <div className="space-y-2">
            {subagents.map((sub) => {
              const isSelected = sub.id === selectedId;
              return (
                <button
                  key={sub.id}
                  data-testid={`subagent-item-${sub.id}`}
                  onClick={() => handleSelectSubagent(sub)}
                  className={`w-full text-left p-3.5 rounded-xl border transition-all flex items-start gap-3.5 ${
                    isSelected
                      ? "bg-surface-elevated border-brand/50 shadow-sm shadow-brand/10"
                      : "bg-surface-card/60 border-surface-border hover:bg-surface-elevated/60 hover:border-surface-border"
                  }`}
                >
                  <div className="p-2 rounded-lg bg-surface-base border border-surface-border shrink-0 mt-0.5">
                    {getSubagentIcon(sub.id)}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold text-foreground truncate">
                        {sub.name}
                      </span>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {sub.built_in ? (
                          <span className="px-1.5 py-0.5 rounded text-[9px] bg-neutral-800 text-neutral-400 border border-neutral-700">
                            Built-in
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[9px] bg-brand/10 text-brand border border-brand/30">
                            Custom
                          </span>
                        )}
                        {sub.enabled ? (
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                            Active
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-neutral-200 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 border border-surface-border">
                            Off
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1 line-clamp-2 leading-relaxed">
                      {sub.description}
                    </p>
                    <div className="flex items-center gap-2 mt-2 text-[10px] text-neutral-500">
                      <span>{sub.max_turns === 0 ? "No step limit" : `Max ${sub.max_turns} steps`}</span>
                      <span>•</span>
                      <span>
                        {sub.whitelisted_tools.includes("*")
                          ? "All tools"
                          : `${sub.whitelisted_tools.length} tools`}
                      </span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Anti-recursion Safety Notice */}
          <div className="p-3.5 rounded-xl border border-surface-border bg-surface-card/40 space-y-2 text-xs text-neutral-500 dark:text-neutral-400">
            <div className="flex items-center gap-1.5 text-neutral-700 dark:text-neutral-300 font-medium">
              <Lock className="w-3.5 h-3.5 text-brand" />
              <span>Recursion Safeguard</span>
            </div>
            <p className="text-[11px] leading-relaxed text-neutral-500">
              Subagents inherit the session workspace but are strictly prohibited from delegating tasks to other subagents. Single-level delegation guarantees zero runaway loops.
            </p>
          </div>
        </div>

        {/* Right: Subagent Configuration Form */}
        <div className="lg:col-span-8 space-y-6 bg-surface-card/60 p-6 rounded-2xl border border-surface-border">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-surface-border pb-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-surface-base border border-surface-border">
                {getSubagentIcon(formData.id)}
              </div>
              <div>
                <h2 data-testid="selected-subagent-title" className="text-lg font-bold text-foreground">
                  {formData.name}
                </h2>
                <div className="flex items-center gap-2 text-xs text-brand">
                  <span>{formData.role_title}</span>
                  <span className="text-neutral-600">•</span>
                  <span className="font-mono text-neutral-400">id: {formData.id}</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {formData.built_in && (formData.customized || !formData.enabled) && (
                <button
                  type="button"
                  onClick={handleDeleteOrReset}
                  disabled={isDeleting}
                  data-testid="reset-subagent-button"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-amber-400 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 transition"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span>Reset Default</span>
                </button>
              )}

              {formData.built_in && formData.enabled && (
                <button
                  type="button"
                  onClick={handleDeleteOrReset}
                  disabled={isDeleting}
                  data-testid="disable-subagent-button"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-neutral-400 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 transition"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Disable Specialist</span>
                </button>
              )}

              {!formData.built_in && (
                <button
                  type="button"
                  onClick={handleDeleteOrReset}
                  disabled={isDeleting}
                  data-testid="delete-subagent-button"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-red-400 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 transition"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Delete Specialist</span>
                </button>
              )}

              <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-neutral-700 dark:text-neutral-300 ml-2">
                <input
                  type="checkbox"
                  checked={formData.enabled}
                  onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
                  className="w-4 h-4 rounded-lg border-surface-border bg-surface-base text-brand focus:ring-brand/40"
                />
                <span>Enabled for Agent</span>
              </label>
            </div>
          </div>

          {/* Form Fields */}
          <div className="space-y-4">
            <Field label="Specialist Name" hint="Display name for this specialist in fleet selectors and logs.">
              {(control) => (
                <Input
                  {...control}
                  data-testid="subagent-name"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                />
              )}
            </Field>

            <Field label="Specialist Role Title">
              {(control) => (
                <Input
                  {...control}
                  data-testid="subagent-role-title"
                  value={formData.role_title}
                  onChange={(e) => setFormData({ ...formData, role_title: e.target.value })}
                />
              )}
            </Field>

            <Field label="Description / Purpose">
              {(control) => (
                <Input
                  {...control}
                  data-testid="subagent-description"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                />
              )}
            </Field>

            <Field
              label="Model"
              hint="Empty inherits the parent turn's model. Select a dedicated model to run subagent delegations on a specialized or lower-cost model."
            >
              {(control) => (
                <ModelPicker
                  {...control}
                  data-testid="subagent-model"
                  value={formData.model || ""}
                  onChange={(model) => setFormData({ ...formData, model: model || null })}
                  inheritLabel="Parent turn model"
                />
              )}
            </Field>

            <div>
              <label className="block text-xs font-semibold text-neutral-400 mb-1.5">
                Specialist System Prompt & Operational Guidance
              </label>
              <textarea
                aria-label="Specialist System Prompt & Operational Guidance"
                rows={6}
                value={formData.system_prompt}
                onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                className="w-full px-3 py-2.5 text-xs bg-surface-base border border-surface-border rounded-lg text-neutral-200 font-mono leading-relaxed focus:outline-none focus:border-brand/70 resize-y"
              />
              <p className="text-[11px] text-neutral-500 mt-1">
                Defines the behavioral persona and constraints given to this specialist when invoked by the lead agent.
              </p>
            </div>

            {/* Sliders: Max Turns and Temperature */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
              <div className="p-4 rounded-xl border border-surface-border bg-surface-base/60 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-neutral-400">
                    Step limit
                  </span>
                  <span className="text-xs font-bold text-brand">
                    {formData.max_turns === 0 ? "until done" : `${formData.max_turns} steps`}
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={1}
                  value={formData.max_turns}
                  onChange={(e) => setFormData({ ...formData, max_turns: parseInt(e.target.value) })}
                  className="w-full accent-brand cursor-pointer"
                />
                <p className="text-[10px] text-neutral-500">
                  Steps before the subagent must report back to the main agent (0: until it has finished).
                </p>
              </div>

              <div className="p-4 rounded-xl border border-surface-border bg-surface-base/60 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-neutral-400">
                    Sampling Temperature
                  </span>
                  <span className="text-xs font-bold text-brand">
                    {formData.temperature}
                  </span>
                </div>
                <input
                  type="range"
                  min={0.0}
                  max={1.0}
                  step={0.05}
                  value={formData.temperature}
                  onChange={(e) => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                  className="w-full accent-brand cursor-pointer"
                />
                <p className="text-[10px] text-neutral-500">
                  Lower values provide deterministic and analytical precision.
                </p>
              </div>
            </div>

            {/* Tool Whitelist */}
            <div className="space-y-3 pt-2">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <span className="block text-xs font-semibold text-neutral-300">
                    Whitelisted Specialist Tools
                  </span>
                  <p className="text-[11px] text-neutral-500">
                    Grant the subagent access only to the necessary capabilities for its specific role.
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={toggleAllTools}
                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border transition ${
                      isAllTools
                        ? "bg-brand/20 text-brand border-brand/40"
                        : "bg-surface-base text-neutral-400 border-surface-border hover:bg-surface-elevated"
                    }`}
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>{isAllTools ? "Wildcard Active (*)" : "Allow All Tools (*)"}</span>
                  </button>
                </div>
              </div>

              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-neutral-500" />
                <input
                  type="text"
                  placeholder="Filter available tools..."
                  value={toolSearch}
                  onChange={(e) => setToolSearch(e.target.value)}
                  className="w-full pl-8 pr-3 py-1.5 text-xs bg-surface-base border border-surface-border rounded-lg text-neutral-200 focus:outline-none focus:border-brand/60"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-1 max-h-72 overflow-y-auto pr-1">
                {filteredTools.map((tool) => {
                  const isChecked = isAllTools || formData.whitelisted_tools.includes(tool.id);
                  return (
                    <label
                      key={tool.id}
                      onClick={() => toggleTool(tool.id)}
                      className={`p-3 rounded-lg border flex items-start gap-2.5 cursor-pointer transition-colors ${
                        isChecked
                          ? "bg-surface-elevated border-brand/40 text-neutral-200"
                          : "bg-surface-base/40 border-surface-border text-neutral-400 hover:bg-surface-elevated/40"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => {}}
                        className="mt-0.5 w-3.5 h-3.5 rounded border-surface-border bg-surface-base text-brand focus:ring-brand/40"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1">
                          <span className="text-xs font-semibold block text-neutral-200 truncate font-mono">
                            {tool.name}
                          </span>
                          {tool.category && (
                            <span className="text-[9px] px-1 py-0.2 rounded bg-neutral-800 text-neutral-400 shrink-0">
                              {tool.category}
                            </span>
                          )}
                        </div>
                        <span className="text-[10px] text-neutral-500 block leading-tight mt-0.5 line-clamp-2">
                          {tool.desc}
                        </span>
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* New Specialist Modal */}
      {showNewModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-surface-card border border-surface-border rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-foreground font-semibold">
                <Wrench className="w-4 h-4 text-brand" />
                <span>Create New Specialist Role</span>
              </div>
              <button
                onClick={() => setShowNewModal(false)}
                className="p-1 rounded-lg text-neutral-400 hover:text-neutral-200 hover:bg-surface-elevated transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-xs text-neutral-400 leading-relaxed">
              Define a unique role ID for this autonomous delegate. The lead agent can invoke it using{" "}
              <code className="text-brand">delegate_subagent</code>.
            </p>

            <Field label="Specialist Role ID" hint="1-32 chars of a-z, 0-9, '_' or '-'. Example: database_specialist">
              {(control) => (
                <Input
                  {...control}
                  data-testid="new-subagent-id-input"
                  value={newSubagentId}
                  onChange={(e) => {
                    setNewSubagentId(e.target.value);
                    setNewSubagentError(null);
                  }}
                  placeholder="e.g. database_specialist"
                  autoFocus
                />
              )}
            </Field>

            {newSubagentError && (
              <div className="p-2.5 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-xs">
                {newSubagentError}
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowNewModal(false)}
                className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-neutral-400 hover:text-neutral-200 transition"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleCreateSpecialist}
                data-testid="create-subagent-confirm"
                className="px-4 py-1.5 rounded-lg text-xs font-medium bg-brand hover:bg-brand/90 text-white transition shadow-sm"
              >
                Create Specialist
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
