"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, ChevronRight, Lock, RotateCcw } from "lucide-react";
import { InheritanceBadge, type Provenance } from "./InheritanceBadge";
import { useSettingsFormHandle } from "./SettingsSave";
import { humanize, settingLabel } from "../../lib/settings-labels";
import { useAccess } from "../../lib/access";

type Kind = "bool" | "int" | "float" | "str" | "enum" | "list" | "dict";
/** Who decides a setting (`config_engine.policy`). */
type Policy = "instance" | "org" | "org_default" | "member";

interface FieldSchema {
  key: string;
  kind: Kind;
  default: unknown;
  policy: Policy;
  /** Lower levels may lower this number, never raise it. */
  ceiling?: boolean;
  /** Environment variables holding the operator's value. */
  instance_env?: string[];
  description: string;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  label?: string;
  unit?: string;
  /** Under `org_default`: false when only presets (a Slack bot), not members, may override. */
  member_override?: boolean;
  advanced?: boolean;
  deprecated?: boolean;
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
  const { isAdmin, loaded: accessLoaded } = useAccess();
  const canEdit = mode === "user" || isAdmin;

  const [schema, setSchema] = useState<FieldSchema[]>([]);
  const [values, setValues] = useState<FieldValues>({});
  const [initial, setInitial] = useState<FieldValues>({});
  const [lockedKeys, setLockedKeys] = useState<string[]>([]);
  const [initialLocked, setInitialLocked] = useState<string[]>([]);
  // Organization mode: the platform default of each key, to show what was changed and reset it.
  const [defaults, setDefaults] = useState<FieldValues>({});
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [orgLocked, setOrgLocked] = useState<string[]>([]);
  // Where each value comes from (platform, operator, organization, the member's own).
  const [provenance, setProvenance] = useState<Record<string, Provenance>>({});
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const fields = useMemo(() => {
    // Organization forms also show operator values (read-only); member forms only what a
    // member may choose.
    const writable = schema.filter((f) =>
      mode === "org"
        ? f.policy !== "member"
        : f.policy === "member" || (f.policy === "org_default" && f.member_override !== false),
    );
    // A superseded setting only shows while it still holds a stored value.
    const visible = writable.filter(
      (f) => !exclude.includes(f.key) && (!f.deprecated || f.key in initial),
    );
    if (!only) return visible;
    return only.map((key) => visible.find((f) => f.key === key)).filter((f): f is FieldSchema => !!f);
  }, [schema, mode, exclude, only, initial]);

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
        source = { ...(data.instance ?? {}), ...data.config };
        setLockedKeys(data.locked_keys ?? []);
        setInitialLocked(data.locked_keys ?? []);
        setDefaults(data.system_defaults ?? {});
        setProvenance(data.provenance ?? {});
      } else {
        const res = await fetch(`/v1/settings/${domain}`);
        if (!res.ok) throw new Error(`settings HTTP ${res.status}`);
        const data = await res.json();
        source = data.effective;
        setOrgLocked(data.locked_keys ?? []);
        setProvenance(data.provenance ?? {});
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

  // Loading before the role is known reads the member view, then reloads as an administrator
  // and overwrites whatever was typed in between.
  useEffect(() => {
    if (accessLoaded) load();
  }, [load, accessLoaded]);

  const issueFor = (key: string) => issues.find((i) => i.key === key)?.message;

  const changedKeys = useMemo(
    () => fields.filter((f) => JSON.stringify(values[f.key]) !== JSON.stringify(initial[f.key])).map((f) => f.key),
    [fields, values, initial],
  );
  const lockChanges =
    mode === "org"
      ? lockedKeys.filter((k) => !initialLocked.includes(k)).length + initialLocked.filter((k) => !lockedKeys.includes(k)).length
      : 0;
  const changes = loaded ? changedKeys.length + lockChanges : 0;

  const save = async (): Promise<boolean> => {
    setSaving(true);
    setBanner(null);
    const local: FieldIssue[] = [];
    const payload: Record<string, unknown> = {};
    for (const field of fields) {
      if (field.policy === "instance") continue;
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
      return false;
    }
    try {
      let res: Response;
      if (mode === "org") {
        // Keep keys this form does not render exactly as stored.
        const keep: Record<string, unknown> = {};
        for (const field of schema) {
          if (field.policy === "instance" || field.policy === "member") continue;
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
        return false;
      }
      if (!res.ok) throw new Error(`Request failed with HTTP ${res.status}`);
      setIssues([]);
      setBanner({ kind: "ok", text: `${title} saved.` });
      await load();
      return true;
    } catch (err) {
      setBanner({ kind: "error", text: err instanceof Error ? err.message : "Save failed" });
      return false;
    } finally {
      setSaving(false);
    }
  };

  const discard = () => {
    setValues(initial);
    setLockedKeys(initialLocked);
    setIssues([]);
    setBanner(null);
  };

  useSettingsFormHandle({ id: key, title, changes, save, discard });

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
    const locked = (mode === "user" && orgLocked.includes(field.key)) || field.policy === "instance";
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
              className="h-4 w-4 accent-orange-500"
            />
            <span className="text-xs text-foreground">{value ? "On" : "Off"}</span>
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
          <div className="relative">
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
              className={`${inputClass} ${field.unit ? "pr-20" : ""}`}
            />
            {field.unit && (
              <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-[11px] text-neutral-500">
                {field.unit}
              </span>
            )}
          </div>
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

  const anchorId = (fieldKey: string) => `setting-${key}-${fieldKey}`;
  const common = fields.filter((f) => !f.advanced);
  const advanced = fields.filter((f) => f.advanced);

  // Follow a link from the settings search: open Advanced if needed, scroll, and mark the field.
  useEffect(() => {
    if (!loaded) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const follow = () => {
      const target = window.location.hash.replace(/^#/, "");
      const match = fields.find((f) => target === anchorId(f.key));
      if (!match) return;
      if (match.advanced) setAdvancedOpen(true);
      setHighlight(match.key);
      timer = setTimeout(() => document.getElementById(target)?.scrollIntoView({ block: "center", behavior: "smooth" }), 50);
    };
    follow();
    window.addEventListener("hashchange", follow);
    return () => {
      window.removeEventListener("hashchange", follow);
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);

  const renderField = (field: FieldSchema) => {
    const issue = issueFor(field.key);
    const memberLocked = mode === "user" && orgLocked.includes(field.key);
    const modified = changedKeys.includes(field.key);
    const atDefault =
      mode === "org" && field.key in defaults && JSON.stringify(values[field.key]) === JSON.stringify(toEditable(field, defaults[field.key]));
    return (
      <div
        key={field.key}
        id={anchorId(field.key)}
        className={`bg-surface-card rounded-xl border p-4 space-y-2 transition-shadow ${
          issue ? "border-rose-700" : modified ? "border-brand/60" : "border-surface-border"
        } ${highlight === field.key ? "ring-2 ring-brand" : ""} ${field.kind === "list" || field.kind === "dict" ? "md:col-span-2" : ""}`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor={`settings-field-${key}-${field.key}`} className="text-xs font-medium text-foreground">
            {settingLabel(field)}
            {field.deprecated ? " (deprecated)" : ""}
          </label>
          <span className="flex flex-wrap items-center gap-2">
            {modified && <span className="text-[10px] font-medium text-brand">Edited</span>}
            {mode === "org" && !atDefault && canEdit && field.key in defaults && (
              <button
                type="button"
                data-testid={`settings-default-${key}-${field.key}`}
                onClick={() => setValues((prev) => ({ ...prev, [field.key]: toEditable(field, defaults[field.key]) }))}
                className="text-[10px] text-neutral-500 underline-offset-2 hover:text-foreground hover:underline"
              >
                Reset to default
              </button>
            )}
            {memberLocked && (
              <span className="inline-flex items-center gap-1 rounded-lg border border-brand/20 bg-brand/10 px-1.5 py-0.5 text-[10px] text-brand">
                <Lock className="h-3 w-3" /> LOCKED BY ORG
              </span>
            )}
            {provenance[field.key] && !memberLocked && (
              <span data-testid={`settings-inheritance-${key}-${field.key}`}>
                <InheritanceBadge
                  provenance={provenance[field.key]}
                  onReset={mode === "user" ? () => resetField(field.key) : undefined}
                />
              </span>
            )}
            {field.ceiling && (
              <span
                className="text-[10px] text-neutral-500"
                title="Agents and members may lower this value, never raise it"
              >
                Ceiling
              </span>
            )}
            {mode === "org" && field.policy === "org_default" && canEdit && (
              <label className="inline-flex items-center gap-1 text-[10px] text-neutral-500 dark:text-neutral-400">
                <input
                  type="checkbox"
                  data-testid={`settings-lock-${key}-${field.key}`}
                  checked={lockedKeys.includes(field.key)}
                  // The stored locks arrive with the values and would undo a click made first.
                  disabled={!loaded}
                  onChange={(e) =>
                    setLockedKeys((prev) => (e.target.checked ? [...prev, field.key] : prev.filter((k) => k !== field.key)))
                  }
                />
                {field.member_override === false ? "Lock for bots" : "Lock for members"}
              </label>
            )}
          </span>
        </div>
        {renderControl(field)}
        <p className="text-[11px] leading-snug text-neutral-600 dark:text-neutral-400">{field.description}</p>
        {issue && <p className="text-[11px] text-rose-500">{issue}</p>}
      </div>
    );
  };

  return (
    <section className="space-y-4" data-testid={`settings-form-${key}`}>
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-surface-border pb-2">
        <div>
          <h2 className="font-display text-base font-semibold text-foreground">{title}</h2>
          {description && <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">{description}</p>}
        </div>
        {mode === "user" && loaded && canEdit && (
          <button
            type="button"
            onClick={reset}
            className="inline-flex items-center gap-1.5 text-xs text-neutral-500 hover:text-foreground dark:text-neutral-400"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Reset my overrides
          </button>
        )}
      </div>

      {!canEdit && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-800/40 bg-amber-950/30 p-3 text-xs text-amber-300">
          <Lock className="h-4 w-4 shrink-0" />
          <span>Read-only: these organization policies require an Administrator or Owner role.</span>
        </div>
      )}

      {banner && (
        <div
          // Success is announced by the page's save bar; a rejection is announced here.
          role={banner.kind === "error" ? "status" : undefined}
          className={`flex items-center gap-2 rounded-xl border p-3 text-xs ${
            banner.kind === "ok"
              ? "border-emerald-800/60 bg-emerald-950/60 text-emerald-400"
              : "border-rose-800/60 bg-rose-950/60 text-rose-400"
          }`}
        >
          {banner.kind === "ok" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          <span>{banner.text}</span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{common.map(renderField)}</div>

      {advanced.length > 0 && (
        <div className="rounded-xl border border-surface-border">
          <button
            type="button"
            data-testid={`settings-advanced-${key}`}
            aria-expanded={advancedOpen || advanced.some((f) => issueFor(f.key))}
            onClick={() => setAdvancedOpen((open) => !open)}
            className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-xs font-medium text-neutral-700 hover:text-foreground dark:text-neutral-300"
          >
            <ChevronRight className={`h-4 w-4 transition-transform ${advancedOpen || advanced.some((f) => issueFor(f.key)) ? "rotate-90" : ""}`} aria-hidden="true" />
            Advanced
            <span className="text-neutral-500">
              ({advanced.length}
              {advanced.some((f) => changedKeys.includes(f.key)) ? ", edited" : ""})
            </span>
          </button>
          {(advancedOpen || advanced.some((f) => issueFor(f.key))) && (
            <div className="grid grid-cols-1 gap-4 border-t border-surface-border p-4 md:grid-cols-2">{advanced.map(renderField)}</div>
          )}
        </div>
      )}
    </section>
  );
}

export default SchemaSettingsForm;
