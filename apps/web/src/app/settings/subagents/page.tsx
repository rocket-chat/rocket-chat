"use client";

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
} from "lucide-react";

const DEFAULT_SUBAGENTS: SubagentConfig[] = [
  {
    id: "security_auditor",
    name: "Security & Penetration Auditor",
    role_title: "Application Security Specialist",
    description: "Performs static vulnerability audits, credential leak scans, and OWASP checks.",
    system_prompt: `You are an autonomous Security Auditor specialist subagent.
Your goal is to inspect code, dependency manifests, and configurations for vulnerabilities, hardcoded secrets, injection vectors, and authorization flaws.
Be concise, surgical, and provide actionable remediation patches or suggestions.`,
    model: null,
    max_turns: 4,
    whitelisted_tools: ["file_read", "bash_exec", "web_search"],
    temperature: 0.1,
    enabled: true,
  },
  {
    id: "qa_verifier",
    name: "QA & Test Verifier",
    role_title: "Automated Test Specialist",
    description: "Runs test suites, analyzes failures, and synthesizes missing regression tests.",
    system_prompt: `You are an autonomous QA & Verification specialist subagent.
Your goal is to execute test suites, analyze error traces, and verify that changes satisfy functional contracts without regressions.
Run targeted test runners and report pass/fail verdicts clearly.`,
    model: null,
    max_turns: 4,
    whitelisted_tools: ["bash_exec", "file_read", "file_write"],
    temperature: 0.2,
    enabled: true,
  },
  {
    id: "researcher",
    name: "Codebase & Web Researcher",
    role_title: "Codebase Architecture & Doc Researcher",
    description: "Explores symbol hierarchies, traces call paths, and reviews online API documentation.",
    system_prompt: `You are an autonomous Codebase & Web Researcher specialist subagent.
Your goal is to locate symbol definitions, investigate library documentation, and synthesize technical findings to unblock the primary agent.
Do not modify files; focus purely on accurate intelligence gathering.`,
    model: null,
    max_turns: 4,
    whitelisted_tools: ["file_read", "web_search", "web_fetch"],
    temperature: 0.2,
    enabled: true,
  },
];

const AVAILABLE_TOOLS = [
  { id: "file_read", name: "File Reader", desc: "Read file slices and analyze syntax" },
  { id: "file_write", name: "File Writer", desc: "Write files and patch content" },
  { id: "bash_exec", name: "Bash Terminal Exec", desc: "Execute commands in isolated sandbox" },
  { id: "web_search", name: "Live Web Search", desc: "Query internet documentation" },
  { id: "web_fetch", name: "URL Content Ingest", desc: "Download and convert documentation URLs" },
  { id: "git_status", name: "Git Status Check", desc: "Inspect git repository status" },
];

export default function SubagentsSettingsPage() {
  const [subagents, setSubagents] = useState<SubagentConfig[]>(DEFAULT_SUBAGENTS);
  const [selectedId, setSelectedId] = useState<string>("security_auditor");
  const [formData, setFormData] = useState<SubagentConfig>(DEFAULT_SUBAGENTS[0]);
  const [isSaved, setIsSaved] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);

  useEffect(() => {
    fetch("/v1/subagents")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setSubagents(data);
          const found = data[0];
          if (found) {
            setSelectedId(found.id);
            setFormData(found);
          }
        }
      })
      .catch(() => {
        // Fallback to defaults
      });
  }, []);

  const handleSelectSubagent = (sub: SubagentConfig) => {
    setSelectedId(sub.id);
    setFormData({ ...sub });
    setIsSaved(false);
  };

  const toggleTool = (toolId: string) => {
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
      // Offline fallback
      setSubagents((prev) =>
        prev.map((s) => (s.id === formData.id ? formData : s))
      );
      setIsSaved(true);
      setTimeout(() => setIsSaved(false), 2500);
    } finally {
      setIsSaving(false);
    }
  };

  const getSubagentIcon = (id: string) => {
    if (id.includes("security")) return <Shield className="w-4 h-4 text-amber-400" />;
    if (id.includes("qa") || id.includes("test")) return <CheckCircle2 className="w-4 h-4 text-emerald-400" />;
    if (id.includes("research")) return <Search className="w-4 h-4 text-sky-400" />;
    return <Cpu className="w-4 h-4 text-brand" />;
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-8 space-y-8 font-sans">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-surface-border pb-6">
        <div>
          <div className="flex items-center gap-2 text-xs font-mono text-brand mb-1 uppercase tracking-wider">
            <Cpu className="w-3.5 h-3.5" />
            <span>Autonomous Specialist Subagents</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Subagents & Task Delegation
          </h1>
          <p className="text-sm text-neutral-400 mt-1">
            Configure autonomous specialists that execute focused, bounded investigation tasks inside the session container.
          </p>
        </div>

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

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Left: Subagents Navigation List */}
        <div className="lg:col-span-4 space-y-3">
          <span className="text-xs font-mono font-semibold text-neutral-400 uppercase tracking-wider block">
            Specialist Fleet ({subagents.length})
          </span>

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
                      <span className="text-sm font-semibold text-neutral-200 truncate">
                        {sub.name}
                      </span>
                      {sub.enabled ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                          Active
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-neutral-800 text-neutral-400 border border-surface-border">
                          Disabled
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-neutral-400 mt-1 line-clamp-2 leading-relaxed">
                      {sub.description}
                    </p>
                    <div className="flex items-center gap-2 mt-2 text-[10px] font-mono text-neutral-500">
                      <span>Max {sub.max_turns} turns</span>
                      <span>•</span>
                      <span>{sub.whitelisted_tools.length} tools</span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Anti-recursion Safety Notice */}
          <div className="p-3.5 rounded-xl border border-surface-border bg-surface-card/40 space-y-2 text-xs text-neutral-400">
            <div className="flex items-center gap-1.5 text-neutral-300 font-medium">
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
          <div className="flex items-center justify-between border-b border-surface-border pb-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-surface-base border border-surface-border">
                {getSubagentIcon(formData.id)}
              </div>
              <div>
                <h2 data-testid="selected-subagent-title" className="text-lg font-bold text-white">{formData.name}</h2>
                <span className="text-xs font-mono text-brand">{formData.role_title}</span>
              </div>
            </div>

            <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-neutral-300">
              <input
                type="checkbox"
                checked={formData.enabled}
                onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
                className="w-4 h-4 rounded border-surface-border bg-surface-base text-brand focus:ring-brand/40"
              />
              <span>Enabled for Agent</span>
            </label>
          </div>

          {/* Form Fields */}
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-mono font-semibold text-neutral-400 mb-1.5 uppercase tracking-wider">
                Specialist Role Title
              </label>
              <input
                type="text"
                value={formData.role_title}
                onChange={(e) => setFormData({ ...formData, role_title: e.target.value })}
                className="w-full px-3 py-2 text-sm bg-surface-base border border-surface-border rounded-lg text-neutral-200 focus:outline-none focus:border-brand/70 font-mono"
              />
            </div>

            <div>
              <label className="block text-xs font-mono font-semibold text-neutral-400 mb-1.5 uppercase tracking-wider">
                Description / Purpose
              </label>
              <input
                type="text"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                className="w-full px-3 py-2 text-sm bg-surface-base border border-surface-border rounded-lg text-neutral-200 focus:outline-none focus:border-brand/70"
              />
            </div>

            <div>
              <label className="block text-xs font-mono font-semibold text-neutral-400 mb-1.5 uppercase tracking-wider">
                Specialist System Prompt & Operational Guidance
              </label>
              <textarea
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
                  <span className="text-xs font-mono font-semibold text-neutral-400 uppercase tracking-wider">
                    Turn Limit Cap
                  </span>
                  <span className="text-xs font-mono font-bold text-brand">
                    {formData.max_turns} turns
                  </span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={8}
                  step={1}
                  value={formData.max_turns}
                  onChange={(e) => setFormData({ ...formData, max_turns: parseInt(e.target.value) })}
                  className="w-full accent-brand cursor-pointer"
                />
                <p className="text-[10px] text-neutral-500">
                  Maximum execution loops before synthesizing findings back to the main agent.
                </p>
              </div>

              <div className="p-4 rounded-xl border border-surface-border bg-surface-base/60 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono font-semibold text-neutral-400 uppercase tracking-wider">
                    Sampling Temperature
                  </span>
                  <span className="text-xs font-mono font-bold text-brand">
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
            <div className="space-y-2.5 pt-2">
              <span className="block text-xs font-mono font-semibold text-neutral-400 uppercase tracking-wider">
                Whitelisted Specialist Tools
              </span>
              <p className="text-[11px] text-neutral-500">
                Grant the subagent access only to the necessary capabilities for its specific role.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-1">
                {AVAILABLE_TOOLS.map((tool) => {
                  const isChecked = formData.whitelisted_tools.includes(tool.id);
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
                      <div className="min-w-0">
                        <span className="text-xs font-semibold block text-neutral-200 font-mono">
                          {tool.name}
                        </span>
                        <span className="text-[10px] text-neutral-500 block leading-tight">
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
    </div>
  );
}
