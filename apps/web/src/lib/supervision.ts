/** The supervision levels a mission or template can run with (the API's permission modes). */
export const SUPERVISION_OPTIONS: { value: string; label: string }[] = [
  { value: "plan", label: "Plan (read-only)" },
  { value: "ask", label: "Ask before changes" },
  { value: "auto_edit", label: "Auto-edit files" },
  { value: "auto", label: "Fully automatic" },
];
