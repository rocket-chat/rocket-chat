"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, Lock, Save, RotateCcw } from "lucide-react";
import { useSession } from "../AuthProvider";
import { InheritanceBadge, type InheritanceStatus } from "./InheritanceBadge";

type Kind = "bool" | "int" | "float" | "str" | "enum" | "list" | "dict";
type Scope = "org" | "user" | "both";

interface FieldSchema {
  key: string;
  kind: Kind;
  default: unknown;
  scope: Scope;
  description: string;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
}

interface FieldIssue {
  key: string;
  message: string;
}

interface SchemaSettingsFormProps {
  /** Settings domain declared in the backend registry (e.g. "sandboxes"). */
  domain: string;
  /** "org": administrators edit organization defaults. "user": a member edits personal overrides. */
  mode: "org" | "user";
  title: string;
  description?: string;
  /** Registry keys to leave out of this form (shown elsewhere). */
  exclude?: string[];
  /** Only these keys (in this order) when set. */
  only?: string[];
  /** Render a text field as a select over these options (e.g. agent personas). */
  selectOptions?: Record<string, { value: string; label: string }[]>;
  /** Distinguishes two forms of one domain on a page (ids must be unique): "mine" for the
   * member's copy of settings an administrator also edits there. */
  idScope?: string;
  /** Render these keys with a dedicated control (e.g. the model picker). */
  controls?: Record<string, (field: CustomControl) => React.ReactNode>;
}

/** What a dedicated control receives for its field. */
export interface CustomControl {
  id: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}

type FieldValues = Record<string, unknown>;

const inputClass =
  "w-full px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs text-foreground focus:outline-none focus:border-brand disabled:opacity-60 disabled:cursor-not-allowed";

function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function toEditable(field: FieldSchema, value: unknown): unknown {
  if (field.kind === "list") return Array.isArray(value) ? (value as string[]).join("\n") : "";
  if (field.kind === "dict") return JSON.stringify(value ?? {}, null, 2);
  if (value === null || value === undefined) return "";
  return value;
}

function fromEditable(field: FieldSchema, value: unknown): { value: unknown; error?: string } {
  switch (field.kind) {
    case "list":
      return {
        value: String(value ?? "")
          .split(/[\n,]/)
          .map((item) => item.trim())
          .filter(Boolean),
      };
    case "dict":
      try {
        const parsed: unknown = JSON.parse(String(value || "{}"));
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          return { value: null, error: "must be a JSON object" };
        }
        return { value: parsed };
      } catch {
        return { value: null, error: "is not valid JSON" };
      }
    case "int":
    case "float":
      if (value === "" || value === null) return { value: field.default === null ? null : field.default };
      return { value: Number(value) };
    default:
      return { value };
  }
}

export function SchemaSettingsForm({
  domain,
  mode,
  title,
  description,
  exclude = [],
  only,
  selectOptions,
  controls,
  idScope,
}: SchemaSettingsFormProps) {
  const key = idScope ? `${idScope}-${domain}` : domain;
  const { data: session } = useSession();
  const roles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || roles.includes("admin") || roles.includes("owner");
  const canEdit = mode === "user" || isAdmin;

  const [schema, setSchema] = useState<FieldSchema[]>([]);
  const [values, setValues] = useState<FieldValues>({});
  const [initial, setInitial] = useState<FieldValues>({});
  const [lockedKeys, setLockedKeys] = useState<string[]>([]);
  const [orgLocked, setOrgLocked] = useState<string[]>([]);
  // Member mode: whether each key follows the organization or is the member's own.
  const [fieldStatus, setFieldStatus] = useState<Record<string, InheritanceStatus>>({});
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const fields = useMemo(() => {
    const writable = schema.filter((f) =>
      mode === "org" ? f.scope === "org" || f.scope === "both" : f.scope === "user" || f.scope === "both",
    );
    const visible = writable.filter((f) => !exclude.includes(f.key));
    if (!only) return visible;
    return only.map((key) => visible.find((f) => f.key === key)).filter((f): f is FieldSchema => !!f);
  }, [schema, mode, exclude, only]);

  const load = useCallback(async () => {
    try {
      const schemaRes = await fetch("/v1/settings-schema");
      if (!schemaRes.ok) throw new Error(`schema HTTP ${schemaRes.status}`);
      const allFields: FieldSchema[] = (await schemaRes.json()).domains[domain] ?? [];
      setSchema(allFields);

      const byKey = Object.fromEntries(allFields.map((f) => [f.key, f]));
      let source: FieldValues;
      if (mode === "org" && isAdmin) {
        const res = await fetch(`/v1/admin/settings/${domain}`);
        if (!res.ok) throw new Error(`settings HTTP ${res.status}`);
        const data = await res.json();
        source = data.config;
        setLockedKeys(data.locked_keys ?? []);
      } else {
        const res = await fetch(`/v1/settings/${domain}`);
        if (!res.ok) throw new Error(`settings HTTP ${res.status}`);
        const data = await res.json();
        source = data.effective;
        setOrgLocked(data.locked_keys ?? []);
        setFieldStatus(data.field_status ?? {});
      }
      const editable: FieldValues = {};
      for (const [key, raw] of Object.entries(source)) {
        if (byKey[key]) editable[key] = toEditable(byKey[key], raw);
      }
      setValues(editable);
      setInitial(editable);
      setLoaded(true);
    } catch (err) {
      setBanner({ kind: "error", text: err instanceof Error ? err.message : "Failed to load settings" });
    }
  }, [domain, mode, isAdmin]);

  useEffect(() => {
    load();
  }, [load]);

  const issueFor = (key: string) => issues.find((i) => i.key === key)?.message;

  const save = async () => {
    setSaving(true);
    setBanner(null);
    const local: FieldIssue[] = [];
    const payload: Record<string, unknown> = {};
    for (const field of fields) {
      const edited = values[field.key];
      if (mode === "user" && JSON.stringify(edited) === JSON.stringify(initial[field.key])) continue;
      if (orgLocked.includes(field.key) && mode === "user") continue;
      const parsed = fromEditable(field, edited);
      if (parsed.error) local.push({ key: field.key, message: parsed.error });
      payload[field.key] = parsed.value;
    }
    if (local.length) {
      setIssues(local);
      setSaving(false);
      return;
    }
    try {
      let res: Response;
      if (mode === "org") {
        // Keep keys this form does not render exactly as stored.
        const keep: Record<string, unknown> = {};
        for (const field of schema) {
          if (!(field.key in payload) && field.key in initial) {
            keep[field.key] = fromEditable(field, initial[field.key]).value;
          }
        }
        res = await fetch(`/v1/admin/settings/${domain}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ config: { ...keep, ...payload }, locked_keys: lockedKeys }),
        });
      } else {
        res = await fetch(`/v1/settings/${domain}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ overrides: payload }),
        });
      }
      if (res.status === 422) {
        const body = await res.json();
        setIssues(Array.isArray(body.detail) ? body.detail : []);
        setBanner({ kind: "error", text: "Some settings were rejected - see the highlighted fields." });
        return;
      }
      if (!res.ok) throw new Error(`Request failed with HTTP ${res.status}`);
      setIssues([]);
      setBanner({ kind: "ok", text: `${title} saved.` });
      await load();
    } catch (err) {
      setBanner({ kind: "error", text: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setSaving(false);
    }
  };

  const resetField = async (key: string) => {
    const res = await fetch(`/v1/settings/${domain}/${encodeURIComponent(key)}`, { method: "DELETE" });
    if (res.ok) {
      setBanner({ kind: "ok", text: `${humanize(key)} follows the organization again.` });
      await load();
    }
  };

  const reset = async () => {
    const res = await fetch(`/v1/settings/${domain}`, { method: "DELETE" });
    if (res.ok) {
      setBanner({ kind: "ok", text: "Personal overrides cleared." });
      await load();
    }
  };

  const renderControl = (field: FieldSchema) => {
    const locked = mode === "user" && orgLocked.includes(field.key);
    // Until the stored values arrive the inputs show blanks, and the arrival would overwrite
    // anything typed into them, so they stay disabled until then.
    const disabled = !canEdit || locked || !loaded;
    const value = values[field.key];
    const set = (next: unknown) => setValues((prev) => ({ ...prev, [field.key]: next }));
    const testId = `settings-field-${key}-${field.key}`;
    const custom = controls?.[field.key];
    if (custom) {
      return custom({ id: testId, value: String(value ?? ""), disabled, onChange: set });
    }
    switch (field.kind) {
      case "bool":
        return (
          <label className="inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              id={testId}
            data-testid={testId}
              checked={Boolean(value)}
              disabled={disabled}
              onChange={(e) => set(e.target.checked)}
              className="w-4 h-4 accent-orange-500"
            />
            <span className="text-xs text-foreground">{value ? "Enabled" : "Disabled"}</span>
          </label>
        );
      case "enum":
        return (
          <select
            id={testId}
            data-testid={testId}
            value={String(value ?? "")}
            disabled={disabled}
            onChange={(e) => set(e.target.value)}
            className={inputClass}
          >
            {field.choices.map((choice) => (
              <option key={choice} value={choice}>
                {choice}
              </option>
            ))}
          </select>
        );
      case "int":
      case "float":
        return (
          <input
            type="number"
            id={testId}
            data-testid={testId}
            value={String(value ?? "")}
            min={field.minimum ?? undefined}
            max={field.maximum ?? undefined}
            step={field.kind === "int" ? 1 : "any"}
            disabled={disabled}
            onChange={(e) => set(e.target.value)}
            className={inputClass}
          />
        );
      case "list":
      case "dict":
        return (
          <textarea
            id={testId}
            data-testid={testId}
            value={String(value ?? "")}
            rows={field.kind === "dict" ? 4 : 3}
            disabled={disabled}
            placeholder={field.kind === "list" ? "One value per line" : "{}"}
            onChange={(e) => set(e.target.value)}
            className={inputClass}
          />
        );
      default:
        if (selectOptions?.[field.key]) {
          return (
            <select
              id={testId}
            data-testid={testId}
              value={String(value ?? "")}
              disabled={disabled}
              onChange={(e) => set(e.target.value)}
              className={inputClass}
            >
              <option value="">Default agent</option>
              {selectOptions[field.key].map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          );
        }
        return (
          <input
            type="text"
            id={testId}
            data-testid={testId}
            value={String(value ?? "")}
            disabled={disabled}
            onChange={(e) => set(e.target.value)}
            className={inputClass}
          />
        );
    }
  };

  return (
    <section className="space-y-4" data-testid={`settings-form-${key}`}>
      <div className="pb-2 border-b border-surface-border">
        <h2 className="text-base font-display font-semibold text-foreground">{title}</h2>
        {description && <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">{description}</p>}
      </div>

      {!canEdit && (
        <div className="flex items-center gap-2 p-3 rounded-xl bg-amber-950/30 border border-amber-800/40 text-amber-300 text-xs">
          <Lock className="w-4 h-4 shrink-0" />
          <span>Read-only: these organization policies require an Administrator or Owner role.</span>
        </div>
      )}

      {banner && (
        <div
          role="status"
          className={`flex items-center gap-2 p-3 rounded-xl border text-xs ${
            banner.kind === "ok"
              ? "bg-emerald-950/60 border-emerald-800/60 text-emerald-400"
              : "bg-rose-950/60 border-rose-800/60 text-rose-400"
          }`}
        >
          {banner.kind === "ok" ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          <span>{banner.text}</span>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {fields.map((field) => {
          const issue = issueFor(field.key);
          const memberLocked = mode === "user" && orgLocked.includes(field.key);
          return (
            <div
              key={field.key}
              className={`bg-surface-card rounded-xl border p-4 space-y-2 ${
                issue ? "border-rose-700" : "border-surface-border"
              } ${field.kind === "list" || field.kind === "dict" ? "md:col-span-2" : ""}`}
            >
              <div className="flex items-center justify-between gap-2">
                <label htmlFor={`settings-field-${key}-${field.key}`} className="text-xs font-medium text-foreground">{humanize(field.key)}</label>
                {memberLocked && (
                  <span className="inline-flex items-center gap-1 text-[10px] text-brand bg-brand/10 px-1.5 py-0.5 rounded-lg border border-brand/20">
                    <Lock className="w-3 h-3" /> LOCKED BY ORG
                  </span>
                )}
                {mode === "user" && !memberLocked && field.scope === "both" && fieldStatus[field.key] && (
                  <span data-testid={`settings-inheritance-${key}-${field.key}`}>
                    <InheritanceBadge
                      status={fieldStatus[field.key]}
                      onReset={() => resetField(field.key)}
                    />
                  </span>
                )}
                {mode === "org" && field.scope === "both" && canEdit && (
                  <label className="inline-flex items-center gap-1 text-[10px] text-neutral-500 dark:text-neutral-400">
                    <input
                      type="checkbox"
                      data-testid={`settings-lock-${key}-${field.key}`}
                      checked={lockedKeys.includes(field.key)}
                      onChange={(e) =>
                        setLockedKeys((prev) =>
                          e.target.checked ? [...prev, field.key] : prev.filter((k) => k !== field.key),
                        )
                      }
                    />
                    Lock for members
                  </label>
                )}
              </div>
              {renderControl(field)}
              <p className="text-[11px] text-neutral-500 dark:text-neutral-400 leading-snug">{field.description}</p>
              {issue && <p className="text-[11px] text-rose-400">{issue}</p>}
            </div>
          );
        })}
      </div>

      {loaded && canEdit && (
        <div className="flex items-center gap-3">
          <button
            type="button"
            data-testid={`settings-save-${key}`}
            onClick={save}
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-brand text-white text-xs font-semibold hover:opacity-90 disabled:opacity-60"
          >
            <Save className="w-3.5 h-3.5" />
            {saving ? "Saving..." : "Save"}
          </button>
          {mode === "user" && (
            <button
              type="button"
              onClick={reset}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-surface-border text-xs text-neutral-500 dark:text-neutral-400 hover:text-foreground"
            >
              <RotateCcw className="w-3.5 h-3.5" /> Reset to organization defaults
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export default SchemaSettingsForm;
