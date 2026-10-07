"use client";

import React, { useState, useEffect } from "react";
import { Zap, Save, CheckCircle2, AlertCircle, Search, Cpu } from "lucide-react";
import { useMissionStore } from "@/lib/store";

export default function UserModelsSettingsPage() {
  const { availableModels, fetchModels } = useMissionStore();
  const [modelPrefs, setModelPrefs] = useState({
    preferred_model: "openrouter/deepseek/deepseek-v4.1-flash",
    turbo_mode: true,
  });
  const [modelFilter, setModelFilter] = useState("");
  const [saving, setSaving] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  useEffect(() => {
    fetchModels();
    fetch("/v1/settings/user_preferences")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.effective) {
          setModelPrefs((prev) => ({ ...prev, ...data.effective }));
        }
      })
      .catch(() => {});
  }, [fetchModels]);

  const handleSave = async () => {
    setSaving(true);
    setErrorBanner(null);
    setSuccessBanner(null);
    try {
      const res = await fetch("/v1/settings/user_preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: modelPrefs }),
      });
      if (!res.ok) {
        let errMessage = "";
        try {
          const data = await res.json();
          errMessage = data.detail || data.message || JSON.stringify(data);
        } catch {
          errMessage = await res.text();
        }
        throw new Error(errMessage || `Failed to save model preferences (HTTP ${res.status})`);
      }
      setSuccessBanner("Model and latency settings saved");
      setTimeout(() => setSuccessBanner(null), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save model preferences";
      setErrorBanner(msg);
      setTimeout(() => setErrorBanner(null), 6000);
    } finally {
      setSaving(false);
    }
  };

  const filteredModels = availableModels.filter(
    (m) =>
      m.name.toLowerCase().includes(modelFilter.toLowerCase()) ||
      m.id.toLowerCase().includes(modelFilter.toLowerCase()) ||
      m.provider.toLowerCase().includes(modelFilter.toLowerCase())
  );

  // Group models by provider
  const modelsByProvider = filteredModels.reduce<Record<string, typeof availableModels>>((acc, model) => {
    const prov = model.provider || "Other";
    if (!acc[prov]) {
      acc[prov] = [];
    }
    acc[prov].push(model);
    return acc;
  }, {});

  const formatContextK = (ctx?: number) => {
    if (!ctx) return "128k";
    return ctx >= 1000 ? `${Math.round(ctx / 1024)}k` : `${ctx} ctx`;
  };

  return (
    <div className="flex flex-col min-h-full">
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs font-mono text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span>User Preferences</span>
            <span>/</span>
            <span className="text-foreground font-medium">Model Defaults & Turbomode</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Model Defaults & Turbomode Acceleration
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
            Customize default LLM engine selection, temperature sensitivity, and speculative execution speed.
          </p>
        </div>
      </div>

      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-6">
          {successBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs font-mono">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>{successBanner}</span>
            </div>
          )}

          {errorBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs font-mono">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errorBanner}</span>
            </div>
          )}

          <div className="bg-surface-card rounded-xl border border-surface-border p-6 space-y-6">
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                  <Cpu className="w-3.5 h-3.5 text-brand" />
                  <span>Default LLM Selection</span>
                </label>
                <span className="text-[10px] font-mono text-neutral-500 dark:text-neutral-400">
                  {availableModels.length} models available
                </span>
              </div>

              {/* Quick filter input if list is large */}
              {availableModels.length > 5 && (
                <div className="relative mb-2 max-w-md">
                  <Search className="w-3.5 h-3.5 text-neutral-400 absolute left-3 top-2.5 pointer-events-none" />
                  <input
                    type="text"
                    value={modelFilter}
                    onChange={(e) => setModelFilter(e.target.value)}
                    placeholder="Search models by name or provider..."
                    className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground placeholder:text-neutral-400 dark:placeholder:text-neutral-500 focus:outline-none focus:border-brand"
                  />
                </div>
              )}

              <select
                value={modelPrefs.preferred_model}
                onChange={(e) =>
                  setModelPrefs({ ...modelPrefs, preferred_model: e.target.value })
                }
                className="w-full max-w-md px-3 py-2 rounded-lg bg-surface-subnav border border-surface-border text-xs font-mono text-foreground focus:outline-none focus:border-brand cursor-pointer"
              >
                {/* If the current preferred model isn't in filtered list, still show it at the top */}
                {!availableModels.some((m) => m.id === modelPrefs.preferred_model) && (
                  <option value={modelPrefs.preferred_model}>
                    {modelPrefs.preferred_model} (Custom / Active)
                  </option>
                )}

                {Object.entries(modelsByProvider).map(([provider, models]) => (
                  <optgroup key={provider} label={provider} className="bg-surface-sidebar font-sans font-semibold text-neutral-700 dark:text-neutral-300">
                    {models.map((m) => (
                      <option
                        key={m.id}
                        value={m.id}
                        className="bg-surface-subnav font-mono font-normal text-foreground text-xs"
                      >
                        {m.name} ({formatContextK(m.context_window)})
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <p className="text-[11px] text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
                Active ID: <span className="font-mono text-neutral-700 dark:text-neutral-300">{modelPrefs.preferred_model}</span>
              </p>
            </div>

            <div className="flex items-center justify-between pt-4 border-t border-surface-borderSubtle">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-brand" />
                  <span className="text-sm font-medium text-foreground">Turbomode Acceleration</span>
                </div>
                <p className="text-xs text-neutral-500 dark:text-neutral-400">
                  Enables parallel speculative reasoning tokens and hardware prefetching (~62 tok/s).
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
                <input
                  type="checkbox"
                  checked={modelPrefs.turbo_mode}
                  onChange={(e) =>
                    setModelPrefs({ ...modelPrefs, turbo_mode: e.target.checked })
                  }
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-surface-highlight peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-brand" />
              </label>
            </div>
          </div>
        </div>
      </div>

      <div className="sticky bottom-0 z-20 bg-surface-sidebar/95 backdrop-blur-md border-t border-surface-border px-8 py-3.5 flex items-center justify-end">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="px-5 py-2 rounded-lg bg-brand hover:bg-brand-hover text-white text-xs font-display font-semibold flex items-center gap-2 cursor-pointer disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          <span>{saving ? "Saving..." : "Save Changes"}</span>
        </button>
      </div>
    </div>
  );
}
