"use client";

import React, { useState, useEffect, useRef } from "react";
import { useMissionStore } from "../../../lib/store";
import { AgentPersonaConfig } from "../../../types/mission";
import { MarkdownRenderer } from "../../../components/MarkdownRenderer";
import {
  Bot,
  Plus,
  Save,
  CheckCircle2,
  Shield,
  Bug,
  Book,
  Check,
  Eye,
  Edit3,
  AlertCircle,
} from "lucide-react";

const getAgentIcon = (iconName?: string) => {
  switch (iconName) {
    case "shield":
      return <Shield className="w-4 h-4 text-emerald-400" />;
    case "bug":
      return <Bug className="w-4 h-4 text-amber-400" />;
    case "book":
      return <Book className="w-4 h-4 text-purple-400" />;
    case "bot":
    default:
      return <Bot className="w-4 h-4 text-cyan" />;
  }
};

const TOOL_CATEGORIES = [
  {
    title: "Sandbox System & File Tools",
    description: "Core execution environment capabilities running inside the container",
    tools: [
      { id: "bash_exec", name: "Bash Terminal Exec", description: "Execute commands in isolated container sandbox", badge: "CLI" },
      { id: "file_read", name: "File Reader", description: "Read files, syntax-check and slice line ranges", badge: "FS" },
      { id: "file_write", name: "File Writer", description: "Atomic write and replacement edits", badge: "FS" },
      { id: "edit_block", name: "Surgical Block Editor", description: "Targeted chunk replacements and refactoring", badge: "FS" },
      { id: "apply_patch", name: "Patch Applicator", description: "Apply unified diff patches cleanly", badge: "FS" },
      { id: "git_commit", name: "Git Engine Committer", description: "Stage and commit changes with co-author trailers", badge: "GIT" },
      { id: "web_search", name: "Live Web Search", description: "Search web docs, packages, and references", badge: "NET" },
      { id: "web_fetch", name: "URL Content Ingest", description: "Convert HTML/Markdown from documentation URLs", badge: "NET" },
      { id: "ask_question", name: "Decision Gates", description: "Ask human clarification questions during turns", badge: "INT" },
    ],
  },
  {
    title: "MCP Servers (Model Context Protocol)",
    description: "External tool servers connected via stdio or SSE transport",
    tools: [
      { id: "mcp:github", name: "GitHub MCP Integration", description: "Issue tracking, PR reviews, commit exploration", badge: "MCP" },
      { id: "mcp:slack", name: "Slack Channel Relay", description: "Send mission notifications and query channels", badge: "MCP" },
      { id: "mcp:postgres", name: "PostgreSQL Database Inspector", description: "Inspect schemas, run read-only analytical queries", badge: "MCP" },
      { id: "mcp:kubernetes", name: "Kubernetes Cluster Inspector", description: "Examine pod logs, events, and cluster state", badge: "MCP" },
    ],
  },
  {
    title: "Custom Skills & Python Extensions",
    description: "Sandboxed AST-validated Python functions and domain tools",
    tools: [
      { id: "skill:python-sandbox", name: "Interactive Python Exec", description: "Run quick algorithmic scripts and data transformations", badge: "SKILL" },
      { id: "skill:code-analyzer", name: "AST Complexity Analyzer", description: "Detect circular dependencies and antipatterns", badge: "SKILL" },
      { id: "skill:test-synthesizer", name: "Regression Test Synthesizer", description: "Generate pytest and Playwright test fixtures", badge: "SKILL" },
    ],
  },
  {
    title: "Knowledge Bases (PGVector RAG)",
    description: "Vector embeddings and semantically indexed documentation",
    tools: [
      { id: "kb:arch-docs", name: "Architecture & System Specifications", description: "System architecture and component contracts", badge: "RAG" },
      { id: "kb:codebase-index", name: "Codebase Symbol & AST Index", description: "Deep symbol references and call hierarchies", badge: "RAG" },
      { id: "kb:security-policies", name: "Tenant Isolation & Security Rules", description: "RLS, RBAC, and container privilege policies", badge: "RAG" },
    ],
  },
];

const ALL_TOOL_IDS = TOOL_CATEGORIES.flatMap((c) => c.tools.map((t) => t.id));

export default function AgentsSettingsPage() {
  const {
    availableAgents,
    setAvailableAgents,
    activeAgent,
    setActiveAgent,
    availableModels,
    fetchAgents,
  } = useMissionStore();

  const [selectedAgentId, setSelectedAgentId] = useState<string>(
    activeAgent?.id || availableAgents[0]?.id || "persona-general"
  );
  const [formData, setFormData] = useState<AgentPersonaConfig>(
    availableAgents.find((a) => a.id === selectedAgentId) || availableAgents[0]
  );
  const [previewMarkdown, setPreviewMarkdown] = useState(false);
  const [isSaved, setIsSaved] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const prevAgentIdRef = useRef<string | null>(null);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  useEffect(() => {
    if (prevAgentIdRef.current !== selectedAgentId) {
      prevAgentIdRef.current = selectedAgentId;
      const found = availableAgents.find((a) => a.id === selectedAgentId);
      if (found) {
        setFormData(found);
        setSaveError(null);
      }
    }
  }, [selectedAgentId, availableAgents]);

  const isAllowAll = formData.whitelisted_tools?.includes("*");

  const handleToggleAllowAll = () => {
    if (isAllowAll) {
      // Revert to default base tools
      setFormData({ ...formData, whitelisted_tools: ["bash_exec", "file_read", "file_write"] });
    } else {
      // Set to wildcard allow all
      setFormData({ ...formData, whitelisted_tools: ["*"] });
    }
  };

  const handleToolToggle = (toolId: string) => {
    const current = formData.whitelisted_tools || [];
    if (current.includes("*")) {
      // If currently allow-all, switching one off means all tools except that one
      const updated = ALL_TOOL_IDS.filter((t) => t !== toolId);
      setFormData({ ...formData, whitelisted_tools: updated });
      return;
    }

    const updated = current.includes(toolId)
      ? current.filter((t) => t !== toolId)
      : [...current, toolId];
    setFormData({ ...formData, whitelisted_tools: updated });
  };

  const handleSave = async () => {
    setIsSaving(true);
    setSaveError(null);
    try {
      const res = await fetch("/v1/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });

      if (!res.ok) {
        let errorDetail = "";
        try {
          const errorJson = await res.json();
          errorDetail = errorJson.message || errorJson.error || JSON.stringify(errorJson);
        } catch {
          errorDetail = await res.text();
        }
        throw new Error(`Failed to save persona (HTTP ${res.status}): ${errorDetail || res.statusText}`);
      }

      const saved: AgentPersonaConfig = await res.json();
      const updatedList = availableAgents.map((a) => (a.id === saved.id ? saved : a));
      if (!updatedList.some((a) => a.id === saved.id)) {
        updatedList.push(saved);
      }
      setAvailableAgents(updatedList);
      if (activeAgent.id === saved.id) {
        setActiveAgent(saved);
      }
      setIsSaved(true);
      setTimeout(() => setIsSaved(false), 2500);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Unknown error saving persona";
      setSaveError(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCreateNew = () => {
    const newId = `persona-${Date.now().toString(36)}`;
    const newPersona: AgentPersonaConfig = {
      id: newId,
      name: "New Autonomous Agent",
      role_title: "Autonomous Specialist",
      description: "Custom agent persona with tailored system instructions and scoped permissions.",
      system_prompt:
        "You are an autonomous engineering agent specialized in mission-critical development. Verify all solutions with automated tests and follow clean code craftsmanship.",
      model: availableModels[0]?.id || "openrouter/deepseek/deepseek-v4.1-flash",
      whitelisted_tools: ["bash_exec", "file_read"],
      icon: "bot",
    };
    setAvailableAgents([...availableAgents, newPersona]);
    setSelectedAgentId(newId);
    setFormData(newPersona);
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold font-mono tracking-wide text-foreground flex items-center gap-2">
            <Bot className="w-5 h-5 text-cyan" />
            CUSTOM AGENT PERSONAS & CONTEXT
          </h1>
          <p className="text-xs font-mono text-muted-foreground mt-1">
            Configure specialized roles, system directives, model parameters, and scoped tool whitelists.
          </p>
        </div>

        <button
          type="button"
          onClick={handleCreateNew}
          className="flex items-center gap-2 px-3 py-2 rounded border border-cyan/40 bg-cyan/10 hover:bg-cyan/20 text-cyan text-xs font-mono font-semibold transition-all shadow-sm shadow-cyan/5 cursor-pointer"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>NEW AGENT PERSONA</span>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Persona Catalog Cards */}
        <div className="space-y-3">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold px-1">
            AVAILABLE AGENT PERSONAS ({availableAgents.length})
          </div>

          <div className="space-y-2">
            {availableAgents.map((agent) => {
              const isSelected = agent.id === selectedAgentId;
              const isActiveInCockpit = activeAgent.id === agent.id;
              return (
                <div
                  key={agent.id}
                  onClick={() => setSelectedAgentId(agent.id)}
                  className={`p-3 rounded-lg border transition-all cursor-pointer ${
                    isSelected
                      ? "border-cyan bg-cyan/10 shadow-sm shadow-cyan/10"
                      : "border-border/80 bg-surface/40 hover:bg-surface hover:border-border"
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    <div className="w-8 h-8 rounded border border-border/70 bg-void flex items-center justify-center shrink-0">
                      {getAgentIcon(agent.icon)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-xs text-foreground truncate">
                          {agent.name}
                        </span>
                        {isActiveInCockpit && (
                          <span className="text-[9px] font-mono text-cyan bg-cyan/20 px-1.5 py-0.5 rounded border border-cyan/30">
                            ACTIVE
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] font-mono text-cyan/80 mt-0.5">
                        {agent.role_title}
                      </div>
                      <p className="text-[11px] text-muted-foreground line-clamp-2 mt-1 font-sans">
                        {agent.description}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right column: Edit Persona Form */}
        <div className="lg:col-span-2 rounded-lg border border-border/80 bg-surface/40 p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/60 pb-3">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded border border-border/70 bg-void flex items-center justify-center">
                {getAgentIcon(formData.icon)}
              </div>
              <div>
                <h2 className="text-sm font-bold text-foreground font-mono">
                  EDIT: {formData.name}
                </h2>
                <span className="text-[10px] font-mono text-muted-foreground">ID: {formData.id}</span>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setActiveAgent(formData)}
                className="px-3 py-1.5 rounded border border-border hover:border-cyan/50 bg-surface text-xs font-mono text-muted-foreground hover:text-cyan transition-colors"
                title="Activate this agent in the Cockpit"
              >
                Set as Active Cockpit Agent
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={isSaving}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-cyan hover:bg-cyan/90 text-void font-bold text-xs font-mono transition-all shadow-sm shadow-cyan/20 cursor-pointer disabled:opacity-50"
              >
                {isSaved ? (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>SAVED!</span>
                  </>
                ) : (
                  <>
                    <Save className="w-3.5 h-3.5" />
                    <span>{isSaving ? "SAVING..." : "SAVE PERSONA"}</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Error Banner */}
          {saveError && (
            <div
              data-testid="persona-save-error"
              className="p-3 bg-red-950/60 border border-red-500/50 rounded text-xs font-mono text-red-300 flex items-center gap-2"
            >
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <span>{saveError}</span>
            </div>
          )}

          {/* Form Fields */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1 font-semibold">
                AGENT DISPLAY NAME
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-3 py-1.5 rounded border border-border bg-void text-xs font-sans text-foreground focus:outline-none focus:border-cyan"
              />
            </div>

            <div>
              <label className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1 font-semibold">
                ROLE TITLE
              </label>
              <input
                type="text"
                value={formData.role_title}
                onChange={(e) => setFormData({ ...formData, role_title: e.target.value })}
                className="w-full px-3 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
              />
            </div>
          </div>

          <div>
            <label className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1 font-semibold">
              DESCRIPTION
            </label>
            <input
              type="text"
              value={formData.description || ""}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="w-full px-3 py-1.5 rounded border border-border bg-void text-xs font-sans text-foreground focus:outline-none focus:border-cyan"
              placeholder="Brief description of this agent's specialization..."
            />
          </div>

          {/* Default Model */}
          <div>
            <label className="block text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1 font-semibold">
              DEFAULT LLM BACKEND
            </label>
            <select
              value={formData.model}
              onChange={(e) => setFormData({ ...formData, model: e.target.value })}
              className="w-full px-3 py-2 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
            >
              {availableModels.map((m) => (
                <option key={m.id} value={m.id} className="bg-void text-foreground">
                  {m.name} ({m.provider}) - {m.id}
                </option>
              ))}
            </select>
          </div>

          {/* Model Hyperparameters & Reasoning Controls */}
          <div className="p-3 rounded-lg border border-border/80 bg-surface/30 space-y-3">
            <span className="text-[10px] font-mono uppercase tracking-wider text-cyan font-semibold block">
              MODEL HYPERPARAMETERS & REASONING EFFORT
            </span>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">
                  Temperature: {formData.temperature !== undefined ? formData.temperature : 0.2}
                </label>
                <input
                  type="range"
                  min="0.0"
                  max="1.5"
                  step="0.05"
                  value={formData.temperature !== undefined ? formData.temperature : 0.2}
                  onChange={(e) =>
                    setFormData({ ...formData, temperature: parseFloat(e.target.value) })
                  }
                  className="w-full accent-cyan cursor-pointer"
                />
              </div>

              <div>
                <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">
                  Reasoning Effort
                </label>
                <select
                  value={formData.reasoning_effort || "medium"}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      reasoning_effort: e.target.value as "low" | "medium" | "high",
                    })
                  }
                  className="w-full px-2 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
                >
                  <option value="low">Low (Faster)</option>
                  <option value="medium">Medium (Standard)</option>
                  <option value="high">High (Deep Frontier)</option>
                </select>
              </div>

              <div>
                <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">
                  Thinking Budget Tokens
                </label>
                <input
                  type="number"
                  min="0"
                  max="64000"
                  step="1024"
                  value={formData.thinking_budget_tokens || 8192}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      thinking_budget_tokens: parseInt(e.target.value) || 0,
                    })
                  }
                  className="w-full px-2 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
                />
              </div>
            </div>
          </div>

          {/* Sandbox Fleet Configuration */}
          <div className="p-3 rounded-lg border border-border/80 bg-surface/30 space-y-3">
            <span className="text-[10px] font-mono uppercase tracking-wider text-cyan font-semibold block">
              EXECUTION SANDBOX & CONTAINER SPEC
            </span>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">
                  Sandbox Image
                </label>
                <select
                  value={formData.sandbox_image || "python:3.12-slim"}
                  onChange={(e) =>
                    setFormData({ ...formData, sandbox_image: e.target.value })
                  }
                  className="w-full px-2 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
                >
                  <option value="python:3.12-slim">python:3.12-slim (Default)</option>
                  <option value="node:20-slim">node:20-slim (Fullstack)</option>
                  <option value="rust:1.77-slim">rust:1.77-slim (Systems)</option>
                  <option value="golang:1.22-bookworm">golang:1.22-bookworm</option>
                  <option value="ubuntu:22.04">ubuntu:22.04 (Base)</option>
                </select>
              </div>

              <div>
                <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">
                  CPU Limit
                </label>
                <input
                  type="text"
                  value={formData.cpu_limit || "2.0"}
                  onChange={(e) => setFormData({ ...formData, cpu_limit: e.target.value })}
                  placeholder="2.0"
                  className="w-full px-2 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
                />
              </div>

              <div>
                <label className="block text-[10px] font-mono uppercase text-muted-foreground mb-1">
                  Memory Limit
                </label>
                <input
                  type="text"
                  value={formData.memory_limit || "4Gi"}
                  onChange={(e) =>
                    setFormData({ ...formData, memory_limit: e.target.value })
                  }
                  placeholder="4Gi"
                  className="w-full px-2 py-1.5 rounded border border-border bg-void text-xs font-mono text-foreground focus:outline-none focus:border-cyan"
                />
              </div>
            </div>
          </div>

          {/* System Prompt with Rich Markdown Live Preview */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold">
                SYSTEM PROMPT & INSTRUCTION MATRIX
              </label>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPreviewMarkdown(false)}
                  className={`px-2 py-0.5 rounded text-[10px] font-mono flex items-center gap-1 ${
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
                  className={`px-2 py-0.5 rounded text-[10px] font-mono flex items-center gap-1 ${
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
              <div data-testid="markdown-preview-box" className="w-full min-h-[160px] p-3 rounded border border-border bg-void/80 max-h-72 overflow-y-auto">
                <MarkdownRenderer content={formData.system_prompt} />
              </div>
            ) : (
              <textarea
                rows={6}
                value={formData.system_prompt}
                onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                className="w-full px-3 py-2 rounded border border-border bg-void text-xs font-mono text-foreground/90 focus:outline-none focus:border-cyan leading-relaxed"
                placeholder="Enter markdown-formatted system instructions..."
              />
            )}
          </div>

          {/* Whitelisted Tools & Capabilities with Category Groups and Allow-All */}
          <div className="space-y-4 pt-2">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2 border-b border-border/60">
              <div>
                <label className="block text-xs font-mono uppercase tracking-wider text-foreground font-semibold">
                  AGENT ACCESSIBLE TOOLS & CAPABILITIES
                </label>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Authorize tools, MCP servers, Python skills, and PGVector knowledge bases for this agent.
                </p>
              </div>

              {/* Allow All Switch */}
              <button
                type="button"
                onClick={handleToggleAllowAll}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-mono transition-all cursor-pointer ${
                  isAllowAll
                    ? "bg-blue-600/20 border-blue-500 text-blue-400 font-semibold shadow-sm shadow-blue-500/10"
                    : "bg-surface hover:bg-overlay border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                <div
                  className={`w-3.5 h-3.5 rounded border flex items-center justify-center ${
                    isAllowAll ? "bg-blue-600 border-blue-500 text-white" : "border-border"
                  }`}
                >
                  {isAllowAll && <Check className="w-2.5 h-2.5" />}
                </div>
                <span>ALLOW ALL TOOLS</span>
              </button>
            </div>

            {isAllowAll && (
              <div className="p-3 rounded-lg border border-blue-500/30 bg-blue-600/10 text-xs text-blue-300 font-mono flex items-center gap-2">
                <Check className="w-4 h-4 text-blue-400 shrink-0" />
                <span>
                  <strong>Full Access Granted:</strong> This agent is authorized with wildcard permissions (<code>*</code>) across all system sandbox tools, connected MCP servers, Python skills, and knowledge bases.
                </span>
              </div>
            )}

            {/* Categorized Tools Sections */}
            <div className="space-y-4">
              {TOOL_CATEGORIES.map((category) => (
                <div key={category.title} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-mono font-semibold text-foreground/90 uppercase tracking-wide">
                      {category.title}
                    </span>
                    <span className="text-[10px] text-muted-foreground font-mono">
                      {category.description}
                    </span>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {category.tools.map((tool) => {
                      const isChecked = isAllowAll || formData.whitelisted_tools?.includes(tool.id);
                      return (
                        <div
                          key={tool.id}
                          onClick={() => handleToolToggle(tool.id)}
                          className={`flex items-start gap-2.5 p-2.5 rounded-lg border transition-colors cursor-pointer ${
                            isChecked
                              ? "border-blue-500/40 bg-blue-500/5 text-foreground"
                              : "border-border/60 bg-surface/30 text-muted-foreground hover:border-border hover:bg-surface/50"
                          }`}
                        >
                          <div
                            className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 mt-0.5 transition-colors ${
                              isChecked ? "border-blue-500 bg-blue-600 text-white" : "border-border/80"
                            }`}
                          >
                            {isChecked && <Check className="w-3 h-3" />}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center justify-between gap-1">
                              <span className="text-xs font-medium text-foreground truncate">{tool.name}</span>
                              <span className="text-[9px] font-mono px-1 py-0.2 rounded bg-surface border border-border/60 text-muted-foreground shrink-0">
                                {tool.badge}
                              </span>
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
          </div>
        </div>
      </div>
    </div>
  );
}
