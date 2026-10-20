"use client";

import { useShallow } from "zustand/react/shallow";
import React, { useState, useEffect } from "react";
import { Save, CheckCircle2, AlertCircle } from "lucide-react";
import { useMissionStore } from "@/lib/store";
import { ProviderKeysPanel } from "@/components/settings/ProviderKeysPanel";
import { Field } from "@/components/ui/Field";
import { ModelPicker } from "@/components/ui/ModelPicker";

export default function UserModelsSettingsPage() {
  const { fetchModels } = useMissionStore(
    useShallow((state) => ({ fetchModels: state.fetchModels }))
  );
  const [modelPrefs, setModelPrefs] = useState({
    preferred_model: "",
  });
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
        body: JSON.stringify({ overrides: { preferred_model: modelPrefs.preferred_model } }),
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

  return (
    <div className="flex flex-col min-h-full">
      <div className="border-b border-surface-border bg-surface-subnav/50 px-8 py-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400 mb-2">
            <span>Settings</span>
            <span>/</span>
            <span>User Preferences</span>
            <span>/</span>
            <span className="text-foreground font-medium">Model Defaults</span>
          </div>
          <h1 className="text-xl md:text-2xl font-display font-bold text-foreground tracking-tight">
            Model Defaults & Inference
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">
            Choose the default LLM engine used across your sessions.
          </p>
        </div>
      </div>

      <div className="flex-1 p-8">
        <div className="max-w-5xl mx-auto space-y-6">
          {successBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-xs">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>{successBanner}</span>
            </div>
          )}

          {errorBanner && (
            <div className="flex items-center gap-2 p-3.5 rounded-xl bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errorBanner}</span>
            </div>
          )}

          <div className="bg-surface-card rounded-xl border border-surface-border p-6 space-y-6">
            <Field
              label="Preferred model"
              hint="Used when the agent has no model of its own. Empty follows the organization's default."
            >
              {(control) => (
                <ModelPicker
                  {...control}
                  data-testid="preferred-model"
                  value={modelPrefs.preferred_model}
                  onChange={(preferred_model) => setModelPrefs({ ...modelPrefs, preferred_model })}
                  inheritLabel="Organization default"
                />
              )}
            </Field>
          </div>
          <ProviderKeysPanel mode="user" />
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
