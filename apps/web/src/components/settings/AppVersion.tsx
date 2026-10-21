"use client";

import React, { useEffect, useState } from "react";

/** The release the server runs: `v0.5.0`, or `dev` for a build that is not a tagged release. */
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
      {version === "dev" ? "dev" : `v${version}`}
    </span>
  );
};
