"use client";

import { Button } from "../../../components/ui/Button";
import { useConfirm } from "../../../components/ui/ConfirmDialog";
import { PageHeader } from "../../../components/ui/PageHeader";

import { Field, Input, Textarea } from "../../../components/ui/Field";
import React, { useState, useEffect, useCallback, useRef } from "react";
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
  Pencil,
} from "lucide-react";
import {
  UserScopeSection,
  ScopeDivider,
  OrgScopeSection,
  TeamScopeSection,
} from "../../../components/settings/ScopedSettingsSection";
import { StarterSkillsPanel } from "../../../components/settings/StarterSkillsPanel";
import { OwnerBadge, OwnerSelect, useMoveConfirmation } from "../../../components/settings/Owner";
import { useOwnedSections } from "../../../lib/useOwnedSections";
import { ownerFields, ownerKey, ownerLabel, type OwnerKey } from "../../../lib/ownership";

export default function SkillsSettingsPage() {
  const [confirm, confirmDialog] = useConfirm();
  const [confirmMove, moveDialog] = useMoveConfirmation();

  const [skills, setSkills] = useState<CustomSkillConfig[]>([]);
  const [expandedSkillId, setExpandedSkillId] = useState<string | null>(null);

  // Form state
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingSkillId, setEditingSkillId] = useState<string | null>(null);
  // The form is above the lists: opened from a row far down the page it would be out of sight.
  const formRef = useRef<HTMLDivElement>(null);
  const [formOwner, setFormOwner] = useState<OwnerKey>("me");
  const [storedOwner, setStoredOwner] = useState<OwnerKey | null>(null);
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

  useEffect(() => {
    if (!showAddForm) return;
    formRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    // Not the first input: that one is the hidden file picker of the SKILL.md import.
    formRef.current?.querySelector<HTMLInputElement>('input:not([type="file"])')?.focus({ preventScroll: true });
  }, [showAddForm, editingSkillId]);

  const sections = useOwnedSections(skills);
  const { access, teams } = sections;
  const isAdmin = access.isAdmin;
  const formScopeLabel = formOwner === "org" ? "organization" : formOwner === "me" ? "personal" : `team ${ownerLabel(formOwner, teams)}`;

  const handleOpenAddForm = (owner: OwnerKey) => {
    setEditingSkillId(null);
    setStoredOwner(null);
    setFormOwner(owner);
    setTitle("");
    setDescription("");
    setBody("");
    setShowAddForm(true);
  };

  const handleOpenEditForm = (skill: CustomSkillConfig) => {
    setEditingSkillId(skill.id);
    setFormOwner(ownerKey(skill));
    setStoredOwner(ownerKey(skill));
    setTitle(skill.title || skill.name || "");
    setDescription(skill.description || "");
    setBody(skill.body || "");
    setShowAddForm(true);
    setExpandedSkillId(skill.id);
  };

  const handleSaveSkill = async () => {
    if (!title.trim() || !body.trim()) {
      setErrorBanner("Title and Skill Body instructions are required.");
      return;
    }

    if (storedOwner && !(await confirmMove(title.trim(), storedOwner, formOwner))) return;
    try {
      const payload: Record<string, unknown> = {
        title: title.trim(),
        description: description.trim() || undefined,
        body: body.trim(),
        ...ownerFields(formOwner),
      };
      if (editingSkillId) {
        payload.id = editingSkillId;
      }

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
      if (editingSkillId) {
        setSkills((prev) => prev.map((s) => (s.id === editingSkillId ? created : s)));
      } else {
        setSkills((prev) => [...prev, created]);
      }
      setShowAddForm(false);
      setEditingSkillId(null);
      setSuccessBanner(
        editingSkillId
          ? `Updated skill '${created.title || created.name}' (${formScopeLabel})`
          : `Created skill '${created.title || created.name}' (${formScopeLabel})`,
      );
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
        body: JSON.stringify({ markdown: await file.text(), ...ownerFields(formOwner) }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to import SKILL.md");
      }
      const created: CustomSkillConfig = await res.json();
      setSkills((prev) => [...prev.filter((s) => s.id !== created.id), created]);
      setShowAddForm(false);
      setSuccessBanner(`Imported skill '${created.title || created.name}' (${formScopeLabel})`);
      setTimeout(() => setSuccessBanner(null), 4000);
    } catch (err: unknown) {
      setErrorBanner(err instanceof Error ? err.message : "Failed to import SKILL.md");
      setTimeout(() => setErrorBanner(null), 5000);
    }
  };

  const handleDeleteSkill = async (id: string, skillTitle: string) => {
    if (!(await confirm({ title: `Delete the skill “${skillTitle}”?`, message: "Agents can no longer load it.", confirmLabel: "Delete", tone: "danger" }))) return;
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
            <div className="p-2 rounded-lg bg-surface-elevated border border-border shrink-0 text-brand">
              <Code2 className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-foreground truncate">
                  {skillTitle}
                </span>
                <OwnerBadge owned={skill} />
              </div>
              {skill.description && (
                <p className="text-[11px] text-muted-foreground truncate mt-0.5">
                  {skill.description}
                </p>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {canManage && (
              <>
                <button
                  type="button"
                  onClick={() => handleOpenEditForm(skill)}
                  className="p-1.5 rounded-lg hover:bg-surface-elevated text-neutral-500 hover:text-brand transition-colors cursor-pointer"
                  title="Edit skill"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <button
                type="button"
                onClick={() => handleDeleteSkill(skill.id, skillTitle)}
                className="p-1.5 rounded-lg hover:bg-rose-950/40 text-neutral-500 hover:text-rose-400 transition-colors cursor-pointer"
                title="Delete skill"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
              </>
            )}
            <button
              type="button"
              onClick={() => setExpandedSkillId(isExpanded ? null : skill.id)}
              className="p-1 rounded-lg hover:bg-surface-elevated text-neutral-400 cursor-pointer"
            >
              {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {isExpanded && (
          <div className="px-4 py-3 border-t border-border bg-surface/40 space-y-2">
            <div className="text-[10px] text-muted-foreground font-semibold flex items-center gap-1.5">
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
      {confirmDialog}
      {moveDialog}
      {/* Header */}
      <PageHeader
        title="Skills &amp; tools"
        icon={<Code2 className="h-5 w-5" aria-hidden="true" />}
        description="Prompt-based guidelines and procedural knowledge injected into autonomous agents."
      />

      <StarterSkillsPanel isAdmin={isAdmin} />

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

      {/* Add New Skill Drawer / Form */}
      {showAddForm && (
        <div
          ref={formRef}
          data-testid="skill-form"
          className="scroll-mt-4 p-5 rounded-lg border border-brand/40 bg-surface space-y-4 shadow-xl"
        >
          <div className="flex items-center justify-between border-b border-border/60 pb-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-brand">
                {editingSkillId ? "EDIT SKILL" : "CREATE NEW SKILL"}
              </span>
              <span className="px-1.5 py-0.5 rounded-lg text-[9px] bg-brand/10 text-brand border border-brand/20">
                {formOwner === "org" ? "Organization Scope" : formOwner === "me" ? "Personal Scope" : `Team · ${ownerLabel(formOwner, teams)}`}
              </span>
            </div>
            <button
              type="button"
onClick={() => { setShowAddForm(false); setEditingSkillId(null); }}
              className="text-neutral-400 hover:text-white text-xs cursor-pointer"
            >
              ✕ CANCEL
            </button>
          </div>

          <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
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

          <OwnerSelect testId="skill-owner" value={formOwner} options={sections.creatable} onChange={setFormOwner} />

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
            <Button size="sm" variant="ghost" onClick={() => setShowAddForm(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={handleSaveSkill}>
              Save skill
            </Button>
          </div>
        </div>
      )}

      {sections.mine && (
        <UserScopeSection
          title="Personal Skills"
          description="Private instructions, conventions, and procedures you created. Attach them to your custom agents."
          action={
            <Button size="sm" variant="secondary" onClick={() => handleOpenAddForm("me")}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              New skill
            </Button>
          }
        >
          {sections.mine.length > 0 ? (
            <div className="space-y-2.5">{sections.mine.map((s) => renderSkillCard(s, true))}</div>
          ) : (
            <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
              <Code2 className="w-6 h-6 mx-auto text-neutral-500" />
              <p className="text-xs text-muted-foreground">You haven&apos;t created any personal skills yet.</p>
            </div>
          )}
        </UserScopeSection>
      )}

      {sections.teamSections.map((section) => (
        <React.Fragment key={section.key}>
          <ScopeDivider label={`Team ${section.team.name}`} />
          <TeamScopeSection
            title={`Team · ${section.team.name}`}
            description="Skills the team's members use; its administrators manage them."
            canManage={section.canManage}
            action={
              <Button size="sm" variant="secondary" data-testid={`new-team-skill-${section.team.id}`} onClick={() => handleOpenAddForm(section.key)}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                New team skill
              </Button>
            }
          >
            {section.items.length > 0 ? (
              <div className="space-y-2.5">{section.items.map((s) => renderSkillCard(s, section.canManage))}</div>
            ) : (
              <p className="text-xs text-muted-foreground">The team has no skill yet.</p>
            )}
          </TeamScopeSection>
        </React.Fragment>
      ))}

      {sections.org && (
        <>
          {(sections.mine || sections.teamSections.length > 0) && <ScopeDivider label="Organization Scope" />}
          <OrgScopeSection
            title="Organization Skills"
            description="Shared engineering guidelines, compliance standards, and procedures available to all workspace members."
            action={
              <Button size="sm" variant="secondary" onClick={() => handleOpenAddForm("org")}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                New org skill
              </Button>
            }
          >
            {sections.org.length > 0 ? (
              <div className="space-y-2.5">{sections.org.map((s) => renderSkillCard(s, isAdmin))}</div>
            ) : (
              <div className="p-6 rounded-lg border border-dashed border-border text-center space-y-2">
                <ShieldCheck className="w-6 h-6 mx-auto text-neutral-500" />
                <p className="text-xs text-muted-foreground">No organization-wide skills defined.</p>
              </div>
            )}
          </OrgScopeSection>
        </>
      )}
    </div>
  );
}
