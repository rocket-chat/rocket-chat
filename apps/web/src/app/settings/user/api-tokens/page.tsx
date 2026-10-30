"use client";

import React from "react";
import { ExternalLink, KeyRound } from "lucide-react";
import { ApiTokensPanel } from "@/components/settings/ApiTokensPanel";
import { Badge } from "@/components/ui/Badge";
import { Card, SectionTitle } from "@/components/ui/Card";
import { PageBody, PageHeader } from "@/components/ui/PageHeader";
import { useAccess } from "../../../../lib/access";

const API_REFERENCE_URL = "https://rocket-chat.mintlify.site/api/rest";

export default function ApiAccessPage() {
  const { isAdmin } = useAccess();

  return (
    <PageBody>
      <PageHeader
        icon={<KeyRound className="h-5 w-5" aria-hidden="true" />}
        title="API access"
        description="Tokens let scripts and other tools call the API without signing in through the browser."
      />
      <div className="space-y-6">
        <Card className="space-y-2 p-5">
          <SectionTitle title="How tokens authenticate" />
          <ul className="list-disc space-y-1 pl-5 text-sm text-neutral-600 dark:text-neutral-400">
            <li>
              Send <code className="font-mono text-xs">Authorization: Bearer rcpat_…</code> with any REST request, or{" "}
              <code className="font-mono text-xs">?token=rcpat_…</code> when opening the WebSocket.
            </li>
            <li>The token is shown once, when you create it; only a hash is stored. Revoking it stops it at once.</li>
            <li>A token cannot create another token.</li>
          </ul>
          <a
            href={API_REFERENCE_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-sm text-brand hover:underline"
          >
            API reference
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
        </Card>

        <ApiTokensPanel scope="personal" />

        {isAdmin && (
          <section aria-label="Guest tokens (administrators)" className="space-y-2">
            <Badge tone="neutral">Admin only · whole organization</Badge>
            <ApiTokensPanel scope="guest" />
          </section>
        )}
      </div>
    </PageBody>
  );
}
