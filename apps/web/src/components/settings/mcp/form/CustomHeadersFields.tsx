"use client";

import React, { useState } from "react";
import { X } from "lucide-react";
import type { McpServerFormState } from "./useMcpServerForm";

const FIELD = "flex-1 rounded-lg border border-border bg-void px-2.5 py-1 text-xs text-foreground";

/** Headers sent with every request to a remote server, secret ones masked. */
export const CustomHeadersFields: React.FC<{ form: McpServerFormState }> = ({ form }) => {
  const { customHeaders, setCustomHeaders } = form.auth;
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [secret, setSecret] = useState(false);

  const add = () => {
    if (!name || !value) return;
    setCustomHeaders([...customHeaders, { name, value, is_secret: secret }]);
    setName("");
    setValue("");
    setSecret(false);
  };

  return (
    <div className="space-y-2 rounded-lg border border-border bg-surface-card p-3">
      <div className="text-xs font-medium text-neutral-600 dark:text-neutral-300">Custom Header Pairs</div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          aria-label="Header key"
          placeholder="Header Key (e.g. X-Org-ID)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className={FIELD}
        />
        <input
          type={secret ? "password" : "text"}
          aria-label="Header value"
          placeholder="Header Value"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className={FIELD}
        />
        <label className="flex cursor-pointer select-none items-center gap-1 text-[11px] text-muted-foreground">
          <input type="checkbox" checked={secret} onChange={(e) => setSecret(e.target.checked)} className="rounded-lg border-border" />
          <span>Secret</span>
        </label>
        <button type="button" onClick={add} className="rounded-lg border border-border bg-surface-elevated px-2.5 py-1 text-xs text-foreground">
          Add Header
        </button>
      </div>
      {customHeaders.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 pt-1" aria-label="Headers">
          {customHeaders.map((header, index) => (
            <li
              key={`${header.name}-${index}`}
              className="flex items-center gap-1 rounded-lg border border-border bg-surface px-2 py-0.5 text-[10px]"
            >
              <span>
                {header.name}: {header.is_secret ? "••••••••" : header.value}
              </span>
              <button
                type="button"
                aria-label={`Remove the ${header.name} header`}
                onClick={() => setCustomHeaders(customHeaders.filter((_, i) => i !== index))}
                className="text-neutral-500 hover:text-rose-400"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
