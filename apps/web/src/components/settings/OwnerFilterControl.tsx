"use client";

import React from "react";
import { Field, Select } from "../ui/Field";
import { useAccess } from "../../lib/access";
import { useTeams } from "../../lib/teams";
import { useOwnerFilter } from "../../lib/useOwnerFilter";
import { filterTeams, parseOwnerFilter } from "../../lib/ownership";

/**
 * "Showing: Everything · Me · <teams> · Organization": narrows the navigation and every list of
 * owned objects to one owner. A member of an organization without teams has nothing to choose
 * between, so it is not shown to them.
 */
export const OwnerFilterControl: React.FC = () => {
  const access = useAccess();
  const { teams } = useTeams();
  const [owner, setOwner] = useOwnerFilter();
  const listed = filterTeams(access, teams);
  if (!access.loaded || (!access.isAdmin && listed.length === 0)) return null;
  return (
    <div data-testid="owner-filter" className="px-0.5">
      <Field label="Showing">
        {(control) => (
          <Select {...control} data-testid="owner-filter-select" value={owner} onChange={(e) => setOwner(parseOwnerFilter(e.target.value))}>
            <option value="all">Everything</option>
            <option value="me">Me</option>
            {listed.map((team) => (
              <option key={team.id} value={`team:${team.id}`}>
                Team · {team.name}
              </option>
            ))}
            <option value="org">Organization</option>
          </Select>
        )}
      </Field>
    </div>
  );
};
