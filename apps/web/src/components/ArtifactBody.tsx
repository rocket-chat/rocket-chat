"use client";

import React, { useEffect, useMemo, useState } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { InlineDiff } from "./InlineDiff";

interface ArtifactBodyProps {
  type: string;
  content: string;
}

/** A Mermaid diagram, drawn in the browser from its source (strict mode: no scripts, no HTML). */
const MermaidDiagram: React.FC<{ source: string }> = ({ source }) => {
  const [svg, setSvg] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setFailure(null);
    import("mermaid")
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
        const id = `artifact-diagram-${Math.random().toString(36).slice(2)}`;
        return mermaid.render(id, source);
      })
      .then((result) => !cancelled && setSvg(result.svg))
      .catch((err) => !cancelled && setFailure(err instanceof Error ? err.message : "The diagram could not be drawn"));
    return () => {
      cancelled = true;
    };
  }, [source]);

  if (failure) {
    return (
      <div className="space-y-2">
        <p role="alert" className="text-xs text-diff-del">
          This diagram could not be drawn ({failure}). Its source:
        </p>
        <pre className="overflow-auto rounded-lg border border-border bg-surface-base p-3 font-mono text-xs">{source}</pre>
      </div>
    );
  }
  if (!svg) return <p className="text-xs text-muted-foreground">Drawing the diagram…</p>;
  return <div data-testid="artifact-diagram" className="overflow-auto bg-white p-3" dangerouslySetInnerHTML={{ __html: svg }} />;
};

function prettyJson(content: string): string {
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

/** One artifact, shown the way its type calls for. */
export const ArtifactBody: React.FC<ArtifactBodyProps> = ({ type, content }) => {
  const json = useMemo(() => (type === "json" ? prettyJson(content) : ""), [type, content]);

  switch (type) {
    case "diagram":
      return <MermaidDiagram source={content} />;
    case "html":
      // No scripts and no access to this app: the frame is a picture of the page, nothing more.
      return (
        <iframe
          title="HTML artifact"
          data-testid="artifact-html"
          sandbox=""
          srcDoc={content}
          className="h-[70vh] w-full rounded-lg border border-border bg-white"
        />
      );
    case "diff":
      return <InlineDiff diff={content} />;
    case "json":
      return (
        <pre data-testid="artifact-json" className="overflow-auto rounded-lg border border-border bg-surface-base p-3 font-mono text-xs leading-5">
          {json}
        </pre>
      );
    default:
      return (
        <div className="prose prose-sm dark:prose-invert max-w-none rounded-lg border border-border bg-surface-card p-4 shadow-sm">
          <MarkdownRenderer content={content} />
        </div>
      );
  }
};
