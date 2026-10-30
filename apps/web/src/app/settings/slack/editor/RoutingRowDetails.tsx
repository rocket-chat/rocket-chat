"use client";

import React, { useEffect, useState } from "react";
import { Field, Input, Select, Textarea } from "../../../../components/ui/Field";
import type { ResponseMode, RoutingRow } from "./routing";

const INSTRUCTIONS_LIMIT = 2000;

interface Option {
  value: string;
  label: string;
}

/** The sandbox images and tool names a channel row may pick from, loaded once per editor. */
export function useRoutingCatalogue(): { images: Option[]; toolNames: string[] } {
  const [images, setImages] = useState<Option[]>([]);
  const [toolNames, setToolNames] = useState<string[]>([]);
  useEffect(() => {
    fetch("/v1/sandboxes")
      .then((res) => (res.ok ? res.json() : { sandboxes: [] }))
      .then((data: { sandboxes?: { name: string; image: string }[] }) =>
        setImages((data.sandboxes ?? []).map((s) => ({ value: s.image, label: `${s.name} (${s.image})` }))),
      )
      .catch(() => undefined);
    fetch("/v1/tools")
      .then((res) => (res.ok ? res.json() : { tools: [] }))
      .then((data: { tools?: { name: string }[] }) => setToolNames((data.tools ?? []).map((t) => t.name)))
      .catch(() => undefined);
  }, []);
  return { images, toolNames };
}

interface RoutingRowDetailsProps {
  row: RoutingRow;
  index: number;
  catalogue: { images: Option[]; toolNames: string[] };
  onChange: (changes: Partial<RoutingRow>) => void;
}

const ResponseModeField: React.FC<{ value: ResponseMode; onChange: (mode: ResponseMode) => void }> = ({ value, onChange }) => (
  <Field label="Response mode">
    {(props) => (
      <Select {...props} value={value} onChange={(e) => onChange(e.target.value === "thread" || e.target.value === "private" ? e.target.value : "")}>
        <option value="">(Bot default)</option>
        <option value="thread">Reply in thread</option>
        <option value="private">Private</option>
      </Select>
    )}
  </Field>
);

const ChannelDetails: React.FC<RoutingRowDetailsProps> = ({ row, index, catalogue, onChange }) => {
  const [tools, setTools] = useState(row.allowed_tools.join(", "));
  const imageKnown = !row.sandbox_image || catalogue.images.some((i) => i.value === row.sandbox_image);
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <ResponseModeField value={row.response_mode} onChange={(response_mode) => onChange({ response_mode })} />
      <Field label="Sandbox image" hint="Must be an image the organization permits (Settings → Sandboxes).">
        {(props) => (
          <Select {...props} value={row.sandbox_image} onChange={(e) => onChange({ sandbox_image: e.target.value })}>
            <option value="">(Default image)</option>
            {!imageKnown && <option value={row.sandbox_image}>{row.sandbox_image}</option>}
            {catalogue.images.map((image) => (
              <option key={image.value} value={image.value}>
                {image.label}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="Allowed tools" hint="Comma-separated tool names. Empty inherits the organization's scope." className="md:col-span-2">
        {(props) => (
          <Input
            {...props}
            list={`routing-tools-${index}`}
            value={tools}
            onChange={(e) => {
              setTools(e.target.value);
              onChange({ allowed_tools: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) });
            }}
            placeholder="file_read, grep_search, web_search"
          />
        )}
      </Field>
      <datalist id={`routing-tools-${index}`}>
        {catalogue.toolNames.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <Field label="House rules" hint={`${row.instructions.length}/${INSTRUCTIONS_LIMIT} characters`} className="md:col-span-2">
        {(props) => (
          <Textarea
            {...props}
            rows={3}
            maxLength={INSTRUCTIONS_LIMIT}
            value={row.instructions}
            onChange={(e) => onChange({ instructions: e.target.value })}
            placeholder="Answer in French. Never run commands against production."
          />
        )}
      </Field>
    </div>
  );
};

const IntentDetails: React.FC<RoutingRowDetailsProps> = ({ row, onChange }) => (
  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
    <Field label="When to choose it" hint="The classifier reads this to recognise the intent." className="md:col-span-2">
      {(props) => (
        <Textarea
          {...props}
          rows={2}
          value={row.description}
          onChange={(e) => onChange({ description: e.target.value })}
          placeholder="The person reports a bug or a failing test"
        />
      )}
    </Field>
    <ResponseModeField value={row.response_mode} onChange={(response_mode) => onChange({ response_mode })} />
    <label className="flex items-center gap-2 self-end pb-2 text-xs text-foreground">
      <input type="checkbox" checked={row.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} />
      Enabled
    </label>
  </div>
);

/** The rarer settings of one row: house rules, tools and image for a channel; when to choose an intent. */
export const RoutingRowDetails: React.FC<RoutingRowDetailsProps> = (props) =>
  props.row.kind === "channel" ? <ChannelDetails {...props} /> : <IntentDetails {...props} />;
