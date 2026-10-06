"use client";

import React, { useState, useEffect } from "react";
import { DecisionQuestion } from "../types/mission";
import { HelpCircle, CheckCircle2, Send } from "lucide-react";

interface DecisionCardProps {
  decision: DecisionQuestion;
  onSubmit: (selectedOptions: string[], customText?: string) => void;
}

export const DecisionCard: React.FC<DecisionCardProps> = ({ decision, onSubmit }) => {
  const [selected, setSelected] = useState<string>(
    decision.default_recommended_option || decision.options[0] || ""
  );
  const [customText, setCustomText] = useState<string>("");

  // Keyboard shortcut listener: 1, 2, 3 to toggle options
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger if user is typing in custom input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }
      const num = parseInt(e.key, 10);
      if (num >= 1 && num <= decision.options.length) {
        setSelected(decision.options[num - 1]);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [decision.options]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected && !customText.trim()) return;
    onSubmit(selected ? [selected] : [], customText.trim() || undefined);
  };

  return (
    <div className="my-4 rounded-lg border border-cyan/40 bg-elevated/95 p-4 shadow-xl shadow-cyan/5 font-mono text-sm relative overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-200">
      {/* Top indicator bar */}
      <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-cyan via-flame to-cyan" />

      <div className="flex items-center gap-2 mb-3 text-cyan">
        <HelpCircle className="w-4 h-4" />
        <span className="font-bold text-xs uppercase tracking-wider">
          Decision Gate Required
        </span>
      </div>

      <p className="text-foreground font-sans font-medium text-base mb-4">
        {decision.question_text}
      </p>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="space-y-2">
          {decision.options.map((option, idx) => {
            const isSelected = selected === option;
            const isRecommended = option === decision.default_recommended_option;

            return (
              <label
                key={option}
                onClick={() => setSelected(option)}
                className={`flex items-center justify-between p-3 rounded-md border cursor-pointer transition-all ${
                  isSelected
                    ? "border-cyan bg-cyan/10 text-cyan shadow-sm shadow-cyan/20"
                    : "border-border bg-surface/60 hover:bg-surface text-muted-foreground hover:text-foreground"
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="w-5 h-5 rounded border border-border flex items-center justify-center text-xs text-muted-foreground font-mono">
                    {idx + 1}
                  </span>
                  <span className="font-sans text-sm font-medium">
                    {option}
                  </span>
                  {isRecommended && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan/20 text-cyan border border-cyan/30 font-mono">
                      RECOMMENDED
                    </span>
                  )}
                </div>

                <div
                  className={`w-4 h-4 rounded-full border flex items-center justify-center ${
                    isSelected ? "border-cyan bg-cyan text-void" : "border-border"
                  }`}
                >
                  {isSelected && <CheckCircle2 className="w-3.5 h-3.5 stroke-[3]" />}
                </div>
              </label>
            );
          })}
        </div>

        {/* Custom text clarification input */}
        <div className="pt-2">
          <input
            type="text"
            placeholder="Or provide custom steering instructions..."
            value={customText}
            onChange={(e) => setCustomText(e.target.value)}
            className="w-full bg-surface border border-border rounded px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:border-cyan transition-colors"
          />
        </div>

        {/* Action Button */}
        <div className="pt-2 flex justify-end">
          <button
            type="submit"
            className="px-4 py-2 rounded bg-flame hover:bg-flame-hover text-white font-display font-semibold text-xs tracking-wider flex items-center gap-2 shadow-md shadow-flame/20 transition-all active:scale-[0.98]"
          >
            <Send className="w-3.5 h-3.5" />
            <span>CONFIRM DECISION</span>
          </button>
        </div>
      </form>
    </div>
  );
};
