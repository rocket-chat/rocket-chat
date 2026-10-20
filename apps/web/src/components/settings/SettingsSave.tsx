"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Save } from "lucide-react";

/** What one settings form tells the page: how much is unsaved, and how to save or drop it. */
export interface SettingsFormHandle {
  id: string;
  title: string;
  /** Fields (and locks) that differ from what is stored. */
  changes: number;
  /** Saves the form; resolves false when the server rejected something. */
  save: () => Promise<boolean>;
  discard: () => void;
}

interface SettingsSaveApi {
  register: (handle: SettingsFormHandle) => void;
  unregister: (id: string) => void;
}

const SettingsSaveContext = createContext<SettingsSaveApi | null>(null);

/**
 * The one place a settings page is saved. Every form on the page reports its unsaved changes
 * here, and a single bar saves or discards all of them, so there is never a Save button per
 * section to hunt for (or forget) while scrolling.
 */
export const SettingsSaveProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const handles = useRef(new Map<string, SettingsFormHandle>());
  const [version, setVersion] = useState(0);
  const [saving, setSaving] = useState(false);
  const [outcome, setOutcome] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const register = useCallback((handle: SettingsFormHandle) => {
    const known = handles.current.get(handle.id);
    handles.current.set(handle.id, handle);
    if (!known || known.changes !== handle.changes || known.title !== handle.title) {
      setVersion((v) => v + 1);
    }
  }, []);
  const unregister = useCallback((id: string) => {
    if (handles.current.delete(id)) setVersion((v) => v + 1);
  }, []);
  const api = useMemo(() => ({ register, unregister }), [register, unregister]);

  // Reading `version` is what re-renders the bar when a form reports a change.
  void version;
  const dirty = [...handles.current.values()].filter((h) => h.changes > 0);
  const changes = dirty.reduce((sum, h) => sum + h.changes, 0);
  const hasChanges = changes > 0;

  // A new edit after a save starts clean: yesterday's outcome is not about it.
  useEffect(() => {
    if (hasChanges) setOutcome(null);
  }, [hasChanges]);

  useEffect(() => {
    if (changes === 0) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [changes]);

  const saveAll = async () => {
    setSaving(true);
    setOutcome(null);
    let failed = 0;
    for (const handle of dirty) {
      if (!(await handle.save())) failed += 1;
    }
    setSaving(false);
    setOutcome(
      failed === 0
        ? { kind: "ok", text: "All changes saved." }
        : { kind: "error", text: `${failed} section${failed === 1 ? "" : "s"} could not be saved: see the highlighted fields.` },
    );
  };

  const discardAll = () => {
    dirty.forEach((handle) => handle.discard());
    setOutcome(null);
  };

  return (
    <SettingsSaveContext.Provider value={api}>
      {children}
      <div
        data-testid="settings-save-bar"
        className="sticky bottom-0 z-20 border-t border-surface-border bg-surface-base/95 backdrop-blur px-4 py-3 sm:px-8"
        hidden={changes === 0 && !outcome}
      >
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3">
          <p role="status" aria-live="polite" className="min-w-0 flex-1 text-xs">
            {changes > 0 ? (
              <span data-testid="settings-unsaved" className="text-foreground">
                <strong>{changes}</strong> unsaved change{changes === 1 ? "" : "s"}
                {dirty.length > 1 && <span className="text-neutral-500"> in {dirty.length} sections</span>}
              </span>
            ) : outcome ? (
              <span className={`inline-flex items-center gap-1.5 ${outcome.kind === "ok" ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
                {outcome.kind === "ok" ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> : <AlertCircle className="h-4 w-4" aria-hidden="true" />}
                {outcome.text}
              </span>
            ) : null}
            {changes > 0 && outcome?.kind === "error" && (
              <span className="ml-3 inline-flex items-center gap-1.5 text-rose-600 dark:text-rose-400">
                <AlertCircle className="h-4 w-4" aria-hidden="true" />
                {outcome.text}
              </span>
            )}
          </p>
          {changes > 0 && (
            <>
              <button
                type="button"
                data-testid="settings-discard-all"
                onClick={discardAll}
                disabled={saving}
                className="rounded-lg border border-surface-border px-3 py-1.5 text-xs text-neutral-600 hover:text-foreground disabled:opacity-60 dark:text-neutral-300"
              >
                Discard
              </button>
              <button
                type="button"
                data-testid="settings-save-all"
                onClick={saveAll}
                disabled={saving}
                className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-60"
              >
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Save className="h-3.5 w-3.5" aria-hidden="true" />}
                {saving ? "Saving…" : "Save changes"}
              </button>
            </>
          )}
        </div>
      </div>
    </SettingsSaveContext.Provider>
  );
};

/** Registers a form's unsaved changes with the page's save bar (a no-op outside a settings page). */
export function useSettingsFormHandle(handle: SettingsFormHandle): void {
  const api = useContext(SettingsSaveContext);
  const latest = useRef(handle);
  latest.current = handle;
  const { id, title, changes } = handle;

  useEffect(() => {
    if (!api) return;
    // The handle the bar calls always reaches the form's current state.
    api.register({
      id,
      title,
      changes,
      save: () => latest.current.save(),
      discard: () => latest.current.discard(),
    });
  }, [api, id, title, changes]);

  useEffect(() => {
    if (!api) return;
    return () => api.unregister(id);
  }, [api, id]);
}
