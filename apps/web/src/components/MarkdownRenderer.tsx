"use client";

import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
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
      <code className="px-1.5 py-0.5 mx-0.5 rounded text-[11px] font-mono bg-cyan/10 text-cyan border border-cyan/20">
        {children}
      </code>
    );
  }

  return (
    <div className="my-2 rounded border border-border/80 bg-void/90 overflow-hidden shadow-inner">
      <div className="flex items-center justify-between px-3 py-1.5 bg-surface/60 border-b border-border/60 text-[10px] font-mono text-muted-foreground">
        <span className="uppercase tracking-wider font-semibold text-cyan/80">
          {language || "code"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground px-1.5 py-0.5 rounded border border-border/50 hover:border-cyan/40 bg-overlay/50 transition-colors"
          title="Copy code"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-emerald-400" />
              <span className="text-emerald-400">Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3 text-muted-foreground" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <div className="p-3 overflow-x-auto text-xs font-mono text-foreground/90 leading-relaxed">
        <pre className="!bg-transparent !p-0 !m-0">
          <code>{children}</code>
        </pre>
      </div>
    </div>
  );
};

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content, className }) => {
  return (
    <div className={`prose-avionics font-sans text-sm leading-relaxed ${className || ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const isInline = !className && typeof children === "string" && !children.includes("\n");
            return (
              <CodeBlock className={className} inline={isInline} {...props}>
                {children}
              </CodeBlock>
            );
          },
          p({ children }) {
            return <p className="mb-2 last:mb-0 leading-relaxed text-foreground/90">{children}</p>;
          },
          h1({ children }) {
            return (
              <h1 className="text-base font-bold tracking-tight text-foreground mt-4 mb-2 pb-1 border-b border-border/60 font-mono">
                {children}
              </h1>
            );
          },
          h2({ children }) {
            return (
              <h2 className="text-sm font-semibold tracking-tight text-foreground mt-3 mb-1.5 font-mono text-cyan/90">
                {children}
              </h2>
            );
          },
          h3({ children }) {
            return (
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mt-2.5 mb-1 font-mono">
                {children}
              </h3>
            );
          },
          ul({ children }) {
            return <ul className="list-disc pl-5 mb-2 space-y-1 text-foreground/90">{children}</ul>;
          },
          ol({ children }) {
            return <ol className="list-decimal pl-5 mb-2 space-y-1 text-foreground/90">{children}</ol>;
          },
          li({ children }) {
            return <li className="leading-relaxed">{children}</li>;
          },
          blockquote({ children }) {
            return (
              <blockquote className="border-l-2 border-cyan/50 bg-cyan/5 pl-3 py-1.5 my-2 text-foreground/80 italic rounded-r">
                {children}
              </blockquote>
            );
          },
          table({ children }) {
            return (
              <div className="overflow-x-auto my-3 rounded border border-border/70">
                <table className="w-full text-xs font-mono text-left border-collapse">
                  {children}
                </table>
              </div>
            );
          },
          thead({ children }) {
            return <thead className="bg-surface/80 text-muted-foreground border-b border-border/70">{children}</thead>;
          },
          th({ children }) {
            return <th className="px-3 py-2 font-semibold uppercase tracking-wider text-[10px]">{children}</th>;
          },
          td({ children }) {
            return <td className="px-3 py-2 border-t border-border/50 text-foreground/90">{children}</td>;
          },
          a({ href, children }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-cyan underline underline-offset-2 hover:text-cyan/80 transition-colors"
              >
                {children}
              </a>
            );
          },
          hr() {
            return <hr className="border-border/60 my-3" />;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};
