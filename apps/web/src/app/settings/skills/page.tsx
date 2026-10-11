"use client";

import { Field, Input, Textarea } from "../../../components/ui/Field";
import React, { useState, useEffect, useCallback } from "react";
import { CustomSkillConfig } from "../../../types/mission";
import {
  Code2,
  Plus,
  ShieldCheck,
  CheckCircle2,
  AlertCircle,
  Trash2,
  ChevronDown,
  ChevronRight,
  BookOpen,
} from "lucide-react";
import { useSession } from "../../../components/AuthProvider";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
} from "../../../components/settings/ScopedSettingsSection";

export default function SkillsSettingsPage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  const [skills, setSkills] = useState<CustomSkillConfig[]>([]);
  const [expandedSkillId, setExpandedSkillId] = useState<string | null>(null);

  // Form state
  const [showAddForm, setShowAddForm] = useState(false);
  const [formScope, setFormScope] = useState<"user" | "org">("user");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");

  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);

  const fetchSkills = useCallback(async () => {
    try {
      const res = await fetch("/v1/skills");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          setSkills(data);
          if (data.length > 0 && !expandedSkillId) {
            setExpandedSkillId(data[0].id);
          }
        }
      }
    } catch (err) {
      console.warn("Failed fetching skills:", err);
    }
  }, [expandedSkillId]);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const userSkills = skills.filter((s) => s.scope === "user" || !s.scope || s.user_id != null);
  const orgSkills = skills.filter((s) => s.scope === "org" && s.user_id == null);

  const handleOpenAddForm = (scope: "user" | "org") => {
    setFormScope(scope);
    setTitle("");
    setDescription("");
    setBody("");
    setShowAddForm(true);
  };

  const handleSaveSkill = async () => {
    if (!title.trim() || !body.trim()) {
      setErrorBanner("Title and Skill Body instructions are required.");
      return;
    }

    try {
      const payload = {
        title: title.trim(),
        description: description.trim() || undefined,
        body: body.trim(),
        scope: formScope,
      };

      const res = await fetch("/v1/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to create skill");
      }

      const created: CustomSkillConfig = await res.json();
      setSkills((prev) => [...prev, created]);
      setShowAddForm(false);
      setSuccessBanner(`Created skill '${created.title || created.name}' (${formScope} scope)`);
      setTimeout(() => setSuccessBanner(null), 4000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error saving skill";
      setErrorBanner(msg);
      setTimeout(() => setErrorBanner(null), 5000);
    }
  };

  const handleImportSkillFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const res = await fetch("/v1/skills/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ markdown: await file.text(), scope: formScope }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to import SKILL.md");
      }
      const created: CustomSkillConfig = await res.json();
      setSkills((prev) => [...prev.filter((s) => s.id !== created.id), created]);
      setShowAddForm(false);
      setSuccessBanner(`Imported skill '${created.title || created.name}' (${formScope} scope)`);
      setTimeout(() => setSuccessBanner(null), 4000);
    } catch (err: unknown) {
      setErrorBanner(err instanceof Error ? err.message : "Failed to import SKILL.md");
      setTimeout(() => setErrorBanner(null), 5000);
    }
  };

  const handleDeleteSkill = async (id: string, skillTitle: string) => {
    if (!confirm(`Are you sure you want to delete skill '${skillTitle}'?`)) return;
    try {
      const res = await fetch(`/v1/skills/${id}`, { method: "DELETE" });
      if (res.ok) {
        setSkills((prev) => prev.filter((s) => s.id !== id));
        setSuccessBanner(`Deleted skill '${skillTitle}'`);
        setTimeout(() => setSuccessBanner(null), 3000);
      } else {
        const err = await res.json().catch(() => ({}));
        setErrorBanner(err.detail || "Failed to delete skill");
      }
    } catch {
      setErrorBanner("Failed to delete skill");
    }
  };

  const renderSkillCard = (skill: CustomSkillConfig, canManage: boolean) => {
    const isExpanded = expandedSkillId === skill.id;
    const skillTitle = skill.title || skill.name || "Untitled Skill";

    return (
      <div
        key={skill.id}
        className="rounded-lg border border-border bg-surface-card hover:border-brand/30 transition-all overflow-hidden"
      >
        <div className="p-3.5 flex items-center justify-between gap-3">
          <div
            className="flex items-center gap-3 cursor-pointer flex-1 min-w-0"
            onClick={() => setExpandedSkillId(isExpanded ? null : skill.id)}
          >
            <div className="p-2 rounded bg-surface-elevated border border-border shrink-0 text-brand">
              <Code2 className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono font-semibold text-foreground truncate">
                  {skillTitle}
                </span>
                <span className="px-1.5 py-0.2 rounded text-[9px] font-mono uppercase bg-surface border border-border text-neutral-400">
                  {skill.scope || "user"}
                </span>
              </div>
              {skill.description && (
                <p className="text-[11px] font-mono text-muted-foreground truncate mt-0.5">
                  {skill.description}
                </p>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {canManage && (
              <button
                type="button"
                onClick={() => handleDeleteSkill(skill.id, skillTitle)}
                className="p-1.5 rounded hover:bg-rose-950/40 text-neutral-500 hover:text-rose-400 transition-colors cursor-pointer"
                title="Delete skill"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
            <button
              type="button"
              onClick={() => setExpandedSkillId(isExpanded ? null : skill.id)}
              className="p-1 rounded hover:bg-surface-elevated text-neutral-400 cursor-pointer"
            >
              {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {isExpanded && (
          <div className="px-4 py-3 border-t border-border bg-surface/40 space-y-2">
            <div className="text-[10px] font-mono text-muted-foreground uppercase font-semibold flex items-center gap-1.5">
              <BookOpen className="w-3 h-3 text-brand" />
              <span>Skill Prompt Instructions:</span>
            </div>
            <pre className="p-3 rounded-lg bg-surface border border-border text-xs font-mono text-neutral-200 whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto">
              {skill.body}
            </pre>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight text-foreground flex items-center gap-2.5">
            <Code2 className="w-5 h-5 text-brand" />
            Skills &amp; tools
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400 mt-1">
            Prompt-based guidelines and procedural knowledge injected into autonomous agents.
          </p>
        </div>
      </div>

      {errorBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs font-mono">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorBanner}</span>
        </div>
      )}

      {successBanner && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successBanner}</span>
        </div>
      )}

      {/* Add New Skill Drawer / Form */}
      {showAddForm && (
        <div className="p-5 rounded-lg border border-brand/40 bg-surface space-y-4 shadow-xl">
          <div className="flex items-center justify-between border-b border-border/60 pb-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono font-bold text-brand uppercase tracking-wider">
                CREATE NEW SKILL
              </span>
              <span className="px-1.5 py-0.5 rounded text-[9px] font-mono uppercase bg-brand/10 text-brand border border-brand/20">
                {formScope === "org" ? "Organization Scope" : "Personal Scope"}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="text-neutral-400 hover:text-white text-xs font-mono cursor-pointer"
            >
              ✕ CANCEL
            </button>
          </div>

          <label className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
            <span>Have a SKILL.md already?</span>
            <input
              type="file"
              accept=".md,text/markdown"
              data-testid="skill-import-file"
              onChange={(e) => {
                void handleImportSkillFile(e.target.files?.[0]);
                e.target.value = "";
              }}
              className="text-[11px]"
            />
          </label>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Skill Title *">
              {(control) => (
                <Input {...control}
                  placeholder="e.g. Clean Code & SRP Guidelines"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              )}
            </Field>
            <Field label="Short Description">
              {(control) => (
                <Input {...control}
                  placeholder="When and how the agent should apply this skill"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              )}
            </Field>
          </div>

          <Field label="Skill Instructions / Prompt Body *">
            {(control) => (
              <Textarea {...control}
                rows={6}
                placeholder="Detailed Markdown instructions for the agent..."
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
            )}
          </Field>

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-border/60">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="px-3 py-1.5 rounded border border-border text-xs font-mono text-neutral-400 hover:text-white cursor-pointer"
            >
              CANCEL
            </button>
            <button
              type="button"
              onClick={handleSaveSkill}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded border border-brand/40 bg-brand text-slate-950 hover:bg-brand/90 text-xs font-mono font-semibold transition-all cursor-pointer shadow-sm"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>SAVE SKILL</span>
            </button>
          </div>
        </div>
      )}

      {/* TIER 1: USER-SPECIFIC SKILLS */}
      <UserScopeSection
        title="Personal Skills"
        description="Private instructions, conventions, and procedures you created. Attach them to your custom agents."
        action={
          <button
            type="button"
            onClick={() => handleOpenAddForm("user")}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-brand/40 bg-brand/10 hover:bg-brand/20 text-brand text-xs font-mono font-semibold transition-all cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>NEW PERSONAL SKILL</span>
          </button>
        }
      >
        {userSkills.length > 0 ? (
          <div className="space-y-2.5">
            {userSkills.map((s) => renderSkillCard(s, true))}
          </div>
        ) : (
          <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
            <Code2 className="w-6 h-6 mx-auto text-neutral-500" />
            <p className="text-xs font-mono text-muted-foreground">
              You haven&apos;t created any personal skills yet.
            </p>
          </div>
        )}
      </UserScopeSection>

      {/* VISUAL SEPARATOR */}
      <ScopeDivider label="Organization Scope" />

      {/* TIER 2: ORGANIZATION-WIDE SKILLS */}
      <OrgScopeSection
        title="Organization Skills"
        description="Shared engineering guidelines, compliance standards, and procedures available to all workspace members."
        action={
          <button
            type="button"
            onClick={() => handleOpenAddForm("org")}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-cyan/40 bg-cyan/10 hover:bg-cyan/20 text-cyan text-xs font-mono font-semibold transition-all cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>NEW ORG SKILL</span>
          </button>
        }
      >
        {orgSkills.length > 0 ? (
          <div className="space-y-2.5">
            {orgSkills.map((s) => renderSkillCard(s, isAdmin))}
          </div>
        ) : (
          <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
            <ShieldCheck className="w-6 h-6 mx-auto text-neutral-500" />
            <p className="text-xs font-mono text-muted-foreground">
              No organization-wide skills defined.
            </p>
          </div>
        )}
      </OrgScopeSection>
    </div>
  );
}
