"use client";

import React from "react";
import { Select } from "../ui/Field";
import { useDefaultAgentChoices } from "../../lib/default-agent";
import type { CustomControl } from "./SchemaSettingsForm";

interface DefaultAgentPickerProps extends CustomControl {
  /** The empty choice: follow the level above (named after what it gives, when known). */
  inheritLabel: string;
  /** A member's own default may be one of their personal agents. */
  includePersonal?: boolean;
  "data-testid"?: string;
}

/** Which agent new sessions start with: one of the agents the person can use, or the level above. */
export const DefaultAgentPicker: React.FC<DefaultAgentPickerProps> = ({
  id,
  value,
  disabled,
  onChange,
  inheritLabel,
  includePersonal = false,
  "data-testid": testId,
}) => {
  const { agents, loaded } = useDefaultAgentChoices(includePersonal);
  // A stored agent the person can no longer pick still shows, so the select never lies.
  const missing = value && !agents.some((a) => a.id === value);
  return (
    <Select
      id={id}
      data-testid={testId ?? id}
      value={value}
      disabled={disabled || !loaded}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{inheritLabel}</option>
      {agents.map((agent) => (
        <option key={agent.id} value={agent.id}>
          {agent.name}
        </option>
      ))}
      {missing && <option value={value}>{loaded ? `Unavailable agent (${value})` : value}</option>}
    </Select>
  );
};

/** For `SchemaSettingsForm`'s `controls`: the default agent of an organization or a team. */
export function defaultAgentControl(inheritLabel: string): (field: CustomControl) => React.ReactNode {
  return function DefaultAgentControl(field: CustomControl) {
    return <DefaultAgentPicker {...field} inheritLabel={inheritLabel} />;
  };
}
