"use client";

import React, { useState } from "react";
import { Play, Sparkles } from "lucide-react";
import { Field, Input, Textarea } from "../../../../components/ui/Field";

interface TryResult {
  route: string;
  applied: string;
  confidence: number;
  reason: string;
  model: string;
  latency_ms: number;
  probabilities: Record<string, number>;
}

const Probabilities: React.FC<{ result: TryResult }> = ({ result }) => (
  <div className="space-y-1 border-t border-border/40 pt-1.5">
    <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Calibrated Probabilities (System One)</span>
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(result.probabilities).map(([name, probability]) => (
        <div key={name} className="flex items-center gap-1 rounded border border-border/60 bg-surface-card px-2 py-0.5 text-[11px]">
          <span className="font-mono text-muted-foreground">{name}:</span>
          <span className={`font-semibold ${name === result.route ? "text-brand" : "text-foreground"}`}>{Math.round(probability * 100)}%</span>
        </div>
      ))}
    </div>
  </div>
);

/** Classifies a sample message with the bot's saved router, without storing a decision. */
export const RouterTryBox: React.FC<{ botId: string }> = ({ botId }) => {
  const [text, setText] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const [result, setResult] = useState<TryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [trying, setTrying] = useState(false);

  const run = async () => {
    setTrying(true);
    setResult(null);
    setError(null);
    try {
      const res = await fetch(`/v1/admin/slack/bots/${botId}/router/try`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim(), thread_excerpt: excerpt.trim() }),
      });
      const data = await res.json();
      if (res.ok) setResult(data as TryResult);
      else setError(typeof data.detail === "string" ? data.detail : "Router test failed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setTrying(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface-card p-4" data-testid="router-try-box">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
          <Sparkles className="h-3.5 w-3.5 text-brand" aria-hidden="true" /> Try the saved intents
        </span>
        <button
          type="button"
          onClick={() => void run()}
          disabled={trying || !text.trim()}
          data-testid="router-try-submit"
          className="flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
        >
          <Play className="h-3 w-3" aria-hidden="true" /> <span>{trying ? "Classifying..." : "Test route"}</span>
        </button>
      </div>
      <Field label="Sample message">
        {(props) => (
          <Textarea
            {...props}
            rows={2}
            data-testid="router-try-input"
            placeholder="Enter sample message to test classification..."
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="text-xs"
          />
        )}
      </Field>
      <Field label="Thread context (optional)">
        {(props) => <Input {...props} placeholder="Optional thread context excerpt..." value={excerpt} onChange={(e) => setExcerpt(e.target.value)} className="h-8 text-xs" />}
      </Field>
      {error && (
        <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}
      {result && (
        <div className="space-y-2 rounded-lg border border-border bg-surface-subnav p-3 text-xs" role="status">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-foreground">
              Chosen route: <code className="text-brand">{result.route}</code> (applied: <code>{result.applied}</code>)
            </span>
            <span className="text-[10px] text-muted-foreground">
              Confidence: {Math.round(result.confidence * 100)}% · {result.latency_ms}ms
            </span>
          </div>
          {Object.keys(result.probabilities ?? {}).length > 0 && <Probabilities result={result} />}
          <p className="text-[11px] text-neutral-500">{result.reason}</p>
        </div>
      )}
    </div>
  );
};
