"use client";

import React, { useCallback } from "react";
import { Building2, User, Users } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Field, Select } from "../ui/Field";
import { useConfirm } from "../ui/ConfirmDialog";
import { useTeams } from "../../lib/teams";
import { audienceOf, ownerKey, ownerLabel, type OwnerKey, type Owned } from "../../lib/ownership";

/** Who owns an object, as a small pill: You, a team's name, or Org. */
export const OwnerBadge: React.FC<{ owned: Owned; className?: string }> = ({ owned, className }) => {
  const { teams } = useTeams();
  const key = ownerKey(owned);
  const Icon = key === "me" ? User : key === "org" ? Building2 : Users;
  return (
    <Badge
      tone={key === "me" ? "brand" : "neutral"}
      data-testid="owner-badge"
      data-owner={key}
      title={`Used by ${audienceOf(key, teams)}`}
      className={className}
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {key === "org" ? "Org" : ownerLabel(key, teams)}
    </Badge>
  );
};

interface OwnerSelectProps {
  value: OwnerKey;
  /** The owners the person may choose (see `creatableOwners`); the current one is always listed. */
  options: OwnerKey[];
  onChange: (owner: OwnerKey) => void;
  disabled?: boolean;
  label?: string;
  testId?: string;
}

/** The owner of an object being created or edited: who can use it and who can change it. */
export const OwnerSelect: React.FC<OwnerSelectProps> = ({ value, options, onChange, disabled, label = "Owner", testId = "owner-select" }) => {
  const { teams } = useTeams();
  const listed = options.includes(value) ? options : [value, ...options];
  return (
    <Field label={label} hint={`Used by ${audienceOf(value, teams)}.`}>
      {(control) => (
        <Select
          {...control}
          data-testid={testId}
          disabled={disabled || listed.length < 2}
          value={value}
          onChange={(e) => onChange(e.target.value as OwnerKey)}
          className="max-w-[16rem]"
        >
          {listed.map((key) => (
            <option key={key} value={key}>
              {key === "me" ? "Me (personal)" : key === "org" ? "Organization" : `Team · ${ownerLabel(key, teams)}`}
            </option>
          ))}
        </Select>
      )}
    </Field>
  );
};

/**
 * `const [confirmMove, dialog] = useMoveConfirmation()`: before saving an existing object under
 * another owner, `await confirmMove(name, from, to)` says who will use it afterwards.
 */
export function useMoveConfirmation(): [(name: string, from: OwnerKey, to: OwnerKey) => Promise<boolean>, React.ReactNode] {
  const { teams } = useTeams();
  const [confirm, dialog] = useConfirm();
  const confirmMove = useCallback(
    (name: string, from: OwnerKey, to: OwnerKey) =>
      from === to
        ? Promise.resolve(true)
        : confirm({
            title: `Move "${name}" to ${ownerLabel(to, teams) === "You" ? "you" : ownerLabel(to, teams)}?`,
            message: `It is used today by ${audienceOf(from, teams)}. After the move it will be used by ${audienceOf(to, teams)}, and changed by those who manage that owner.`,
            confirmLabel: "Move",
          }),
    [confirm, teams],
  );
  return [confirmMove, dialog];
}
