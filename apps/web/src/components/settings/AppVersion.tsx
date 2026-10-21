"use client";

import React, { useEffect, useState } from "react";

/** The build the server runs: `v0.5.0` for a release, `dev` or `dev-21102026-1036` for any other build. */
export const AppVersion: React.FC = () => {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    fetch("/v1/version")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setVersion(typeof data?.version === "string" ? data.version : null))
      .catch(() => setVersion(null));
  }, []);

  if (!version) return null;
  return (
    <span data-testid="app-version" title="Version of the running server">
      {/^\d/.test(version) ? `v${version}` : version}
    </span>
  );
};
