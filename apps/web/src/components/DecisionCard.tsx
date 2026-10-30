"use client";

import React, { useId, useState } from "react";
import { HelpCircle, Send } from "lucide-react";
import { DecisionQuestion } from "../types/mission";

interface DecisionCardProps {
  decision: DecisionQuestion;
  onSubmit: (selectedOptions: string[], customText?: string) => void;
}

/** A question the agent asks: one choice (radios) or several (checkboxes), and a free answer.
 * Number keys pick an option only while focus is inside the card, never from the composer. */
export const DecisionCard: React.FC<DecisionCardProps> = ({ decision, onSubmit }) => {
  const multi = decision.is_multi_select;
  const recommended = decision.default_recommended_option || "";
  const [selected, setSelected] = useState<string[]>(
    recommended ? [recommended] : multi || decision.options.length === 0 ? [] : [decision.options[0]]
  );
  const [other, setOther] = useState("");
  const groupName = useId();
  const otherId = useId();

  const toggle = (option: string) =>
    setSelected((current) =>
      multi ? (current.includes(option) ? current.filter((o) => o !== option) : [...current, option]) : [option]
    );

  const onKeyDown = (event: React.KeyboardEvent<HTMLFormElement>) => {
    if (event.target instanceof HTMLInputElement && event.target.type === "text") return;
    const index = Number.parseInt(event.key, 10);
    if (index >= 1 && index <= decision.options.length) {
      event.preventDefault();
      toggle(decision.options[index - 1]);
    }
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (selected.length === 0 && !other.trim()) return;
    onSubmit(selected, other.trim() || undefined);
  };

  return (
    <div
      data-testid="decision-card"
      role="group"
      aria-label="The agent asks a question"
      className="rounded-xl border border-cyan/50 bg-surface-card p-4 text-sm shadow-sm"
    >
      <div className="mb-2 flex items-center gap-2 text-cyan-800 dark:text-cyan">
        <HelpCircle className="h-4 w-4" aria-hidden="true" />
        <span className="text-xs font-semibold">
          {decision.subagent_role
            ? `The ${decision.subagent_role.replace(/_/g, " ")} subagent asks`
            : "The agent asks"}
        </span>
      </div>
      <form onSubmit={handleSubmit} onKeyDown={onKeyDown} className="space-y-3">
        <fieldset className="space-y-2">
          <legend className="mb-2 text-base font-medium text-foreground">{decision.question_text}</legend>
          {decision.options.map((option, idx) => {
            const checked = selected.includes(option);
            return (
              <label
                key={option}
                className={`flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors ${
                  checked ? "border-cyan bg-cyan/10" : "border-border hover:bg-surface-elevated"
                }`}
              >
                <input
                  type={multi ? "checkbox" : "radio"}
                  name={groupName}
                  value={option}
                  checked={checked}
                  onChange={() => toggle(option)}
                  data-testid={`decision-option-${idx + 1}`}
                />
                <span className="font-mono text-xs text-neutral-600 dark:text-neutral-400" aria-hidden="true">
                  {idx + 1}
                </span>
                <span className="text-foreground">{option}</span>
                {option === recommended && (
                  <span className="rounded border border-cyan/40 px-1.5 py-0.5 text-[10px] text-cyan-800 dark:text-cyan">
                    Recommended
                  </span>
                )}
              </label>
            );
          })}
        </fieldset>
        <div className="space-y-1">
          <label htmlFor={otherId} className="text-xs font-medium text-neutral-700 dark:text-neutral-300">
            {decision.options.length > 0 ? "Other (or more details)" : "Your answer"}
          </label>
          <input
            id={otherId}
            type="text"
            data-testid="decision-other"
            value={other}
            onChange={(e) => setOther(e.target.value)}
            className="w-full rounded border border-border bg-surface-base px-3 py-2 text-sm text-foreground focus:border-cyan focus:outline-none"
          />
        </div>
        <div className="flex justify-end">
          <button
            type="submit"
            data-testid="decision-submit"
            className="flex items-center gap-2 rounded bg-flame px-4 py-2 text-xs font-semibold text-white hover:bg-flame-hover"
          >
            <Send className="h-3.5 w-3.5" aria-hidden="true" />
            Answer
          </button>
        </div>
      </form>
    </div>
  );
};
