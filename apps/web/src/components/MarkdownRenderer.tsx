"use client";

import React, { useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

interface CodeBlockProps {
  children?: React.ReactNode;
  className?: string;
  inline?: boolean;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ children, className, inline }) => {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || "");
  const language = match ? match[1] : "";
  const codeString = String(children || "").replace(/\n$/, "");

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(codeString);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard write fallback
    }
  };

  if (inline) {
    return (
      <code className="px-1.5 py-[1.5px] mx-0.5 rounded-[5px] text-[12.5px] font-mono font-normal break-words [overflow-wrap:anywhere] bg-neutral-200/60 text-neutral-800 border border-neutral-300/80 dark:bg-neutral-800/70 dark:text-neutral-200 dark:border-neutral-700/50">
        {children}
      </code>
    );
  }

  return (
    <div className="my-3 max-w-full rounded-lg border border-surface-border bg-neutral-950 shadow-sm overflow-hidden">
      <div className="flex items-center justify-between px-3.5 py-1.5 bg-neutral-900/90 border-b border-white/[0.08] text-[11px] font-mono text-neutral-400">
        <span className="font-mono lowercase tracking-normal text-neutral-400">
          {language || "code"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 text-[11px] text-neutral-400 hover:text-neutral-200 px-2 py-0.5 rounded border border-white/10 hover:border-white/20 bg-white/[0.04] transition-colors cursor-pointer"
          title="Copy code"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-emerald-400" />
              <span className="text-emerald-400">Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3 text-neutral-400" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <div className="p-3.5 overflow-x-auto text-[13px] font-mono text-neutral-200 leading-relaxed">
        <pre className="!bg-transparent !p-0 !m-0">
          <code>{children}</code>
        </pre>
      </div>
    </div>
  );
};

/** Built once: a new object on every render would make react-markdown rebuild every element. */
const MARKDOWN_COMPONENTS: Components = {
  code({ className, children, ...props }) {
    const isInline = !className && typeof children === "string" && !children.includes("\n");
    return (
      <CodeBlock className={className} inline={isInline} {...props}>
        {children}
      </CodeBlock>
    );
  },
  p({ children }) {
    return <p className="mb-3.5 last:mb-0 leading-[1.68] text-foreground/90 font-normal">{children}</p>;
  },
  h1({ children }) {
    return (
      <h1 className="text-base font-semibold tracking-tight text-foreground mt-5 mb-2.5 pb-1.5 border-b border-surface-border font-sans">
        {children}
      </h1>
    );
  },
  h2({ children }) {
    return (
      <h2 className="text-[15px] font-semibold tracking-tight text-foreground mt-4 mb-2 font-sans">
        {children}
      </h2>
    );
  },
  h3({ children }) {
    return (
      <h3 className="text-sm font-semibold text-foreground/90 mt-3 mb-1.5 font-sans">
        {children}
      </h3>
    );
  },
  ul({ children }) {
    return (
      <ul className="list-disc pl-5 mb-3.5 space-y-1.5 text-foreground/90 marker:text-neutral-400 dark:marker:text-neutral-500">
        {children}
      </ul>
    );
  },
  ol({ children }) {
    return (
      <ol className="list-decimal pl-5 mb-3.5 space-y-1.5 text-foreground/90 marker:text-neutral-400 dark:marker:text-neutral-500">
        {children}
      </ol>
    );
  },
  li({ children }) {
    return <li className="leading-[1.68] pl-0.5">{children}</li>;
  },
  blockquote({ children }) {
    return (
      <blockquote className="border-l-2 border-neutral-300 dark:border-neutral-700 bg-neutral-100/60 dark:bg-neutral-800/30 pl-3.5 py-1.5 my-3 text-neutral-700 dark:text-neutral-300 italic rounded-r text-[13.5px] leading-relaxed">
        {children}
      </blockquote>
    );
  },
  table({ children }) {
    return (
      <div className="my-3.5 max-w-full overflow-x-auto rounded-lg border border-surface-border shadow-sm">
        <table className="w-full text-xs font-mono text-left border-collapse">
          {children}
        </table>
      </div>
    );
  },
  thead({ children }) {
    return <thead className="bg-surface-elevated/70 text-neutral-500 dark:text-neutral-400 border-b border-surface-border">{children}</thead>;
  },
  th({ children }) {
    return <th className="px-3.5 py-2 font-medium uppercase tracking-wider text-[10.5px]">{children}</th>;
  },
  td({ children }) {
    return <td className="px-3.5 py-2 border-t border-surface-border text-foreground/90 text-xs font-mono">{children}</td>;
  },
  a({ href, children }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all text-brand underline underline-offset-3 hover:text-brand-hover transition-colors font-medium"
      >
        {children}
      </a>
    );
  },
  hr() {
    return <hr className="border-surface-border my-4" />;
  },
};

const REMARK_PLUGINS = [remarkGfm];

/** Memoised: a long conversation re-renders while a turn streams, and only the streaming text changes. */
export const MarkdownRenderer = React.memo(function MarkdownRenderer({ content, className }: MarkdownRendererProps) {
  return (
    <div className={`prose-avionics min-w-0 max-w-full font-sans text-sm leading-relaxed [overflow-wrap:anywhere] ${className || ""}`}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MARKDOWN_COMPONENTS}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
