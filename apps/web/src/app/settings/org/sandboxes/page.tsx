"use client";

import React, { useState } from "react";
import {
  Server,
  Cpu,
  Layers,
  CheckCircle2,
  Plus,
  Trash2,
} from "lucide-react";

interface SandboxPoolConfig {
  default_image: string;
  cpu_limit: string;
  memory_limit: string;
  timeout_seconds: number;
  allowed_images: string[];
}

export default function OrgSandboxesPage() {
  const [config, setConfig] = useState<SandboxPoolConfig>({
    default_image: "python:3.12-slim",
    cpu_limit: "2.0",
    memory_limit: "4Gi",
    timeout_seconds: 600,
    allowed_images: [
      "python:3.12-slim",
      "node:20-slim",
      "rust:1.77-slim",
      "golang:1.22-bookworm",
      "ubuntu:22.04",
    ],
  });

  const [newImageInput, setNewImageInput] = useState("");
  const [isSaved, setIsSaved] = useState(false);

  const handleAddImage = () => {
    const trimmed = newImageInput.trim();
    if (trimmed && !config.allowed_images.includes(trimmed)) {
      setConfig({
        ...config,
        allowed_images: [...config.allowed_images, trimmed],
      });
      setNewImageInput("");
    }
  };

  const handleRemoveImage = (img: string) => {
    if (config.allowed_images.length <= 1) return;
    const filtered = config.allowed_images.filter((i) => i !== img);
    setConfig({
      ...config,
      allowed_images: filtered,
      default_image: config.default_image === img ? filtered[0] : config.default_image,
    });
  };

  const handleSave = () => {
    setIsSaved(true);
    setTimeout(() => setIsSaved(false), 3000);
  };

  return (
    <div className="max-w-4xl space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Server className="w-5 h-5 text-brand" />
          <h1 className="text-xl font-display font-bold text-white tracking-wide">
            ORGANIZATION SANDBOX FLEET & RUNTIMES
          </h1>
        </div>
        <p className="text-xs font-mono text-neutral-400">
          Configure default isolated container images, CPU/RAM ceilings, and allowed OCI runtime images provisioned across your development fleet.
        </p>
      </div>

      {/* Global Defaults Card */}
      <div className="p-5 rounded-xl bg-surface-card border border-surface-border space-y-4">
        <div className="flex items-center justify-between pb-2 border-b border-surface-border">
          <div className="flex items-center gap-2">
            <Cpu className="w-4 h-4 text-brand" />
            <h2 className="text-sm font-display font-semibold text-white uppercase tracking-wider">
              Fleet Default Sandbox Profile
            </h2>
          </div>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-brand/10 text-brand border border-brand/20">
            ENFORCED BY ADMISSION WEBHOOK
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-mono uppercase text-neutral-400 mb-1">
              Default Container Image
            </label>
            <select
              value={config.default_image}
              onChange={(e) => setConfig({ ...config, default_image: e.target.value })}
              className="w-full px-3 py-2 rounded-lg bg-surface-elevated border border-surface-border text-xs font-mono text-white focus:outline-none focus:border-brand"
            >
              {config.allowed_images.map((img) => (
                <option key={img} value={img}>
                  {img} {img === "python:3.12-slim" ? "(Recommended Default)" : ""}
                </option>
              ))}
            </select>
            <p className="text-[10px] font-mono text-neutral-500 mt-1">
              Applied automatically to all new custom agents and ad-hoc sessions unless overridden.
            </p>
          </div>

          <div>
            <label className="block text-xs font-mono uppercase text-neutral-400 mb-1">
              Turn Execution Timeout (Seconds)
            </label>
            <input
              type="number"
              min="30"
              max="3600"
              value={config.timeout_seconds}
              onChange={(e) =>
                setConfig({ ...config, timeout_seconds: parseInt(e.target.value) || 600 })
              }
              className="w-full px-3 py-2 rounded-lg bg-surface-elevated border border-surface-border text-xs font-mono text-white focus:outline-none focus:border-brand"
            />
            <p className="text-[10px] font-mono text-neutral-500 mt-1">
              Max duration allowed for individual tool invocations and commands.
            </p>
          </div>

          <div>
            <label className="block text-xs font-mono uppercase text-neutral-400 mb-1">
              Default CPU Limit (Cores)
            </label>
            <input
              type="text"
              value={config.cpu_limit}
              onChange={(e) => setConfig({ ...config, cpu_limit: e.target.value })}
              className="w-full px-3 py-2 rounded-lg bg-surface-elevated border border-surface-border text-xs font-mono text-white focus:outline-none focus:border-brand"
            />
            <p className="text-[10px] font-mono text-neutral-500 mt-1">
              Cgroup v2 CPU ceiling per container instance.
            </p>
          </div>

          <div>
            <label className="block text-xs font-mono uppercase text-neutral-400 mb-1">
              Default RAM Limit
            </label>
            <input
              type="text"
              value={config.memory_limit}
              onChange={(e) => setConfig({ ...config, memory_limit: e.target.value })}
              className="w-full px-3 py-2 rounded-lg bg-surface-elevated border border-surface-border text-xs font-mono text-white focus:outline-none focus:border-brand"
            />
            <p className="text-[10px] font-mono text-neutral-500 mt-1">
              Max resident memory allocation before kernel OOM killer triggers.
            </p>
          </div>
        </div>
      </div>

      {/* Allowed Images Registry List */}
      <div className="p-5 rounded-xl bg-surface-card border border-surface-border space-y-4">
        <div className="flex items-center justify-between pb-2 border-b border-surface-border">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-brand" />
            <h2 className="text-sm font-display font-semibold text-white uppercase tracking-wider">
              Whitelisted OCI Container Images
            </h2>
          </div>
          <span className="text-[10px] font-mono text-neutral-400">
            {config.allowed_images.length} Approved Images
          </span>
        </div>

        <div className="space-y-2">
          {config.allowed_images.map((img) => {
            const isDefault = img === config.default_image;
            return (
              <div
                key={img}
                className="flex items-center justify-between px-3 py-2 rounded-lg bg-surface-elevated border border-surface-border"
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-white">{img}</span>
                  {isDefault && (
                    <span className="px-1.5 py-0.5 text-[9px] font-mono uppercase rounded bg-brand/20 text-brand border border-brand/30">
                      DEFAULT
                    </span>
                  )}
                </div>

                {!isDefault && (
                  <button
                    type="button"
                    onClick={() => handleRemoveImage(img)}
                    className="text-neutral-500 hover:text-red-400 transition-colors p-1"
                    title="Remove Image"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* Add Image Input */}
        <div className="flex items-center gap-2 pt-2">
          <input
            type="text"
            value={newImageInput}
            onChange={(e) => setNewImageInput(e.target.value)}
            placeholder="e.g. mcr.microsoft.com/devcontainers/python:3.12"
            className="flex-1 px-3 py-2 rounded-lg bg-surface-elevated border border-surface-border text-xs font-mono text-white focus:outline-none focus:border-brand"
          />
          <button
            type="button"
            onClick={handleAddImage}
            className="px-3 py-2 rounded-lg bg-surface-elevated hover:bg-surface-border border border-surface-border text-xs font-mono text-white flex items-center gap-1.5 transition-colors"
          >
            <Plus className="w-3.5 h-3.5 text-brand" />
            <span>Add Image</span>
          </button>
        </div>
      </div>

      {/* Save Button */}
      <div className="flex items-center justify-end gap-3 pt-2">
        {isSaved && (
          <div className="flex items-center gap-1.5 text-xs font-mono text-emerald-400">
            <CheckCircle2 className="w-4 h-4" />
            <span>Sandbox fleet policies persisted</span>
          </div>
        )}
        <button
          type="button"
          onClick={handleSave}
          className="px-4 py-2 rounded-lg bg-brand hover:bg-brand/90 text-white text-xs font-mono font-semibold transition-all shadow-sm"
        >
          SAVE FLEET SETTINGS
        </button>
      </div>
    </div>
  );
}
