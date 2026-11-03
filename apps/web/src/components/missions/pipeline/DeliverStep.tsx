"use client";

import React from "react";
import { Eye, MessageSquare, MonitorPlay, Slack, Webhook, type LucideIcon } from "lucide-react";
import { Field, Input, Select, Textarea } from "../../ui/Field";
import { DELIVERY_OPTIONS, blankDelivery, parseOutputSchema, type DeliveryKind, type MissionDelivery } from "../../../lib/missions";
import { Tile, TileGroup } from "./Tile";
import type { Named } from "./AgentStep";

const DELIVERY_ICONS: Record<string, LucideIcon> = {
  chat: Slack,
  webhook: Webhook,
  github_review: Eye,
  github_comment: MessageSquare,
};

const DeliveryFields: React.FC<{
  delivery: MissionDelivery;
  schemaText: string;
  bots: Named[];
  advanced: boolean;
  onChange: (delivery: MissionDelivery) => void;
  onSchemaText: (text: string) => void;
}> = ({ delivery, schemaText, bots, advanced, onChange, onSchemaText }) => {
  const label = DELIVERY_OPTIONS.find((d) => d.kind === delivery.kind)?.label ?? delivery.kind;
  const schema = delivery.kind === "webhook" ? parseOutputSchema(schemaText) : null;
  return (
    <li className="space-y-3 rounded-lg border border-surface-border p-3" data-testid={`mission-delivery-${delivery.kind}`}>
      <span className="text-xs font-semibold text-foreground">{label}</span>
      {delivery.kind === "chat" && (
        <div className="grid gap-3 sm:grid-cols-2">
          {bots.length > 0 && (
            <Field label="Slack bot">
              {(control) => (
                <Select {...control} value={delivery.bot_id} onChange={(e) => onChange({ ...delivery, bot_id: e.target.value })}>
                  <option value="">The organization&apos;s bot</option>
                  {bots.map((bot) => (
                    <option key={bot.id} value={bot.id}>
                      {bot.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          <Field label="Channel id" hint="C…, G… or D…">
            {(control) => (
              <Input {...control} data-testid="mission-chat-channel" value={delivery.channel} onChange={(e) => onChange({ ...delivery, channel: e.target.value.trim() })} />
            )}
          </Field>
        </div>
      )}
      {delivery.kind === "webhook" && (
        <div className="space-y-3">
          <Field label="URL" hint="An https URL; each call is signed with a secret shown once">
            {(control) => (
              <Input {...control} data-testid="mission-webhook-url" placeholder="https://" value={delivery.url} onChange={(e) => onChange({ ...delivery, url: e.target.value })} />
            )}
          </Field>
          {advanced ? (
            <Field
              label="Result schema (JSON Schema, optional)"
              hint="The object the agent must deliver; empty: a summary."
              error={schema && "error" in schema ? schema.error : null}
            >
              {(control) => (
                <Textarea
                  {...control}
                  data-testid="mission-webhook-schema"
                  rows={5}
                  className="font-mono text-xs"
                  placeholder='{"type": "object", "properties": {"severity": {"enum": ["low", "high"]}}, "required": ["severity"]}'
                  value={schemaText}
                  onChange={(e) => onSchemaText(e.target.value)}
                />
              )}
            </Field>
          ) : (
            <p className="text-xs text-neutral-600 dark:text-neutral-400">
              {schemaText.trim() ? "Sends the object described by the schema set in Advanced." : "Sends a summary; a result schema can be set in Advanced."}
            </p>
          )}
        </div>
      )}
      {(delivery.kind === "github_review" || delivery.kind === "github_comment") && (
        <p className="text-xs text-neutral-600 dark:text-neutral-400">Posted on the pull request or issue the event is about, with the mission&apos;s own access.</p>
      )}
    </li>
  );
};

/** Where the result goes: always its session, and any of Slack, a webhook or the forge. */
export const DeliverStep: React.FC<{
  deliveries: MissionDelivery[];
  schemas: Record<number, string>;
  bots: Named[];
  advanced: boolean;
  onChange: (deliveries: MissionDelivery[]) => void;
  onSchemas: (schemas: Record<number, string>) => void;
}> = ({ deliveries, schemas, bots, advanced, onChange, onSchemas }) => {
  const toggle = (kind: Exclude<DeliveryKind, "session">) => {
    const index = deliveries.findIndex((d) => d.kind === kind);
    if (index < 0) return onChange([...deliveries, blankDelivery(kind)]);
    onChange(deliveries.filter((_, i) => i !== index));
    // Schema texts are keyed by position: those after the removed delivery move up one.
    onSchemas(
      Object.fromEntries(
        Object.entries(schemas)
          .filter(([i]) => Number(i) !== index)
          .map(([i, text]) => [Number(i) > index ? Number(i) - 1 : Number(i), text]),
      ),
    );
  };
  return (
    <div className="space-y-4">
      <TileGroup label="Where the result goes" role="group">
        <Tile icon={MonitorPlay} label="Its session" hint="Every run is a session you can open." selected role="checkbox" disabled testId="mission-delivery-toggle-session" onClick={() => undefined} />
        {DELIVERY_OPTIONS.map((option) => (
          <Tile
            key={option.kind}
            icon={DELIVERY_ICONS[option.kind] ?? Webhook}
            label={option.label}
            role="checkbox"
            selected={deliveries.some((d) => d.kind === option.kind)}
            testId={`mission-delivery-toggle-${option.kind}`}
            onClick={() => toggle(option.kind)}
          />
        ))}
      </TileGroup>
      <ul className="space-y-3">
        {deliveries.map((delivery, index) => (
          <DeliveryFields
            key={delivery.kind}
            delivery={delivery}
            schemaText={schemas[index] ?? ""}
            bots={bots}
            advanced={advanced}
            onChange={(next) => onChange(deliveries.map((d, i) => (i === index ? next : d)))}
            onSchemaText={(text) => onSchemas({ ...schemas, [index]: text })}
          />
        ))}
      </ul>
    </div>
  );
};
