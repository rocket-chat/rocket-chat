"use client";

import React from "react";
import { AlertTriangle, Coins, Info, RefreshCw, Square, Zap } from "lucide-react";
import type { FlightLogEntry, SystemNotice } from "../types/mission";
import { CompactionCard } from "./CompactionCard";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { compactionHeadline, legacyNoticeKind, withoutLeadingEmoji } from "../lib/system-notices";

const Banner: React.FC<{
  testId: string;
  tone: "sky" | "rose";
  icon: React.ReactNode;
  title: string;
  aside?: string;
  children: React.ReactNode;
}> = ({ testId, tone, icon, title, aside, children }) => {
  // Each colour pair keeps AA contrast on the light and on the dark surface.
  const palette =
    tone === "sky"
      ? { box: "border-sky-500/40 bg-sky-500/10", title: "text-sky-800 dark:text-sky-300", aside: "text-sky-800 dark:text-sky-400/80", body: "text-neutral-800 dark:text-neutral-300" }
      : { box: "border-rose-500/40 bg-rose-500/10", title: "text-rose-800 dark:text-rose-300", aside: "text-rose-800 dark:text-rose-400/80", body: "text-rose-900 dark:text-rose-200" };
  return (
    <div data-testid={testId} className="my-3 flex justify-center">
      <div className={`w-full max-w-xl space-y-2 rounded-xl border p-3.5 text-foreground ${palette.box}`}>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {icon}
            <span className={`text-xs font-semibold uppercase tracking-wide ${palette.title}`}>{title}</span>
          </div>
          {aside && <span className={`font-mono text-[10px] ${palette.aside}`}>{aside}</span>}
        </div>
        <div className={`font-sans text-xs leading-relaxed ${palette.body}`}>{children}</div>
      </div>
    </div>
  );
};

const Pill: React.FC<{ kind: string; tone: "neutral" | "amber" | "rose"; icon: React.ReactNode; text: string }> = ({
  kind,
  tone,
  icon,
  text,
}) => {
  const palette = {
    neutral: "border-surface-border bg-surface-card/40 text-neutral-700 dark:text-neutral-400",
    amber: "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-300",
    rose: "border-rose-500/40 bg-rose-500/10 text-rose-800 dark:text-rose-300",
  }[tone];
  return (
    <div data-testid="system-notice" data-kind={kind} className="my-2 flex justify-center">
      <div className={`inline-flex max-w-full items-center gap-2 rounded-full border px-3 py-1 font-mono text-[11px] ${palette}`}>
        {icon}
        <span className="min-w-0 [overflow-wrap:anywhere]">{text}</span>
      </div>
    </div>
  );
};

const iconClass = "h-3.5 w-3.5 shrink-0";

/** The card for a typed notice: every field comes from the event, nothing is parsed back. */
function NoticeCard({ notice, text }: { notice: SystemNotice; text: string }) {
  switch (notice.kind) {
    case "compaction":
      return <CompactionCard content={text} headline={compactionHeadline(notice)} summary={notice.summary} />;
    case "continuation":
      return (
        <Banner
          testId="continuation-card"
          tone="sky"
          icon={<RefreshCw className="h-4 w-4 text-sky-700 dark:text-sky-400" aria-hidden="true" />}
          title="The task continues"
          aside={`Turn ${notice.index} of ${notice.max}`}
        >
          {notice.reason}
        </Banner>
      );
    case "alert":
      return (
        <Banner testId="alert-card" tone="rose" icon={<AlertTriangle className="h-4 w-4 text-rose-700 dark:text-rose-400" aria-hidden="true" />} title="Alert">
          <MarkdownRenderer content={notice.text} />
        </Banner>
      );
    case "step_limit":
      return <Pill kind={notice.kind} tone="amber" icon={<Zap className={iconClass} aria-hidden="true" />} text={text} />;
    case "turn_failed":
    case "sandbox_failed":
    case "send_failed":
      return <Pill kind={notice.kind} tone="rose" icon={<AlertTriangle className={iconClass} aria-hidden="true" />} text={text} />;
    case "turn_stopped":
      return <Pill kind={notice.kind} tone="neutral" icon={<Square className={iconClass} aria-hidden="true" />} text={text} />;
    case "spend_warning":
      return <Pill kind={notice.kind} tone="amber" icon={<Coins className={iconClass} aria-hidden="true" />} text={text} />;
    case "cost":
      return <Pill kind={notice.kind} tone="neutral" icon={<Coins className={iconClass} aria-hidden="true" />} text={text} />;
    case "info":
      return <Pill kind={notice.kind} tone="neutral" icon={<Info className={iconClass} aria-hidden="true" />} text={text} />;
  }
}

/** A system message restored from history, which is text only. */
function LegacyCard({ content }: { content: string }) {
  const text = withoutLeadingEmoji(content);
  switch (legacyNoticeKind(content)) {
    case "compaction":
      return <CompactionCard content={content} />;
    case "continuation":
      return (
        <Banner testId="continuation-card" tone="sky" icon={<RefreshCw className="h-4 w-4 text-sky-700 dark:text-sky-400" aria-hidden="true" />} title="The task continues">
          <MarkdownRenderer content={text} />
        </Banner>
      );
    case "alert":
      return (
        <Banner testId="alert-card" tone="rose" icon={<AlertTriangle className="h-4 w-4 text-rose-700 dark:text-rose-400" aria-hidden="true" />} title="Alert">
          <MarkdownRenderer content={text} />
        </Banner>
      );
    case "step_limit":
      return <Pill kind="step_limit" tone="amber" icon={<Zap className={iconClass} aria-hidden="true" />} text={text} />;
    default:
      return <Pill kind="info" tone="neutral" icon={<Info className={iconClass} aria-hidden="true" />} text={text} />;
  }
}

/** A platform message in the conversation: a typed card for live events, a best reading for history. */
export const SystemNoticeCard: React.FC<{ entry: FlightLogEntry }> = ({ entry }) =>
  entry.notice ? <NoticeCard notice={entry.notice} text={entry.content} /> : <LegacyCard content={entry.content} />;
