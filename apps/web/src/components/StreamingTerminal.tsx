"use client";

// Bundled with the app (not a CDN link) so the terminal works offline.
import "@xterm/xterm/css/xterm.css";

import React, { useEffect, useRef, useState } from "react";
import type { Terminal as XtermType } from "@xterm/xterm";
import type { FitAddon as FitAddonType } from "@xterm/addon-fit";
import { useMissionStore } from "../lib/store";
import { InteractiveTerminal } from "./InteractiveTerminal";
import { ChevronDown, ChevronUp, Terminal as TerminalIcon } from "lucide-react";

export const StreamingTerminal: React.FC<{ collapsed?: boolean; onToggle?: () => void }> = ({ collapsed = false, onToggle }) => {
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermInstanceRef = useRef<XtermType | null>(null);
  const fitAddonRef = useRef<FitAddonType | null>(null);
  const terminalLogs = useMissionStore((state) => state.terminalLogs);
  const terminalBase = useMissionStore((state) => state.terminalBase);
  const terminalEpoch = useMissionStore((state) => state.terminalEpoch);
  const [tab, setTab] = useState<"agent" | "shell">("agent");
  // Characters written so far, counted from the start of the session's output (the store trims
  // the front of a long log, so positions are absolute), and the reset generation they belong to.
  const writtenRef = useRef<number>(0);
  const epochRef = useRef<number>(0);

  useEffect(() => {
    let isMounted = true;

    async function initXterm() {
      if (!terminalRef.current || xtermInstanceRef.current) return;

      const { Terminal } = await import("@xterm/xterm");
      const { FitAddon } = await import("@xterm/addon-fit");

      if (!isMounted) return;

      const term = new Terminal({
        theme: {
          background: "#08090D",
          foreground: "#F8FAFC",
          cursor: "#FF4D00",
          selectionBackground: "rgba(255, 77, 0, 0.3)",
          black: "#1D2332",
          red: "#FF1744",
          green: "#00E676",
          yellow: "#FF8C00",
          blue: "#00F2FE",
          magenta: "#7928CA",
          cyan: "#00F2FE",
          white: "#F8FAFC",
        },
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 12,
        lineHeight: 1.3,
        cursorBlink: true,
        // Command output ends its lines with a bare "\n": without this each line would start where
        // the last one ended and the output would run down the screen in a staircase.
        convertEol: true,
        disableStdin: true, // agent output is read-only; the Shell tab is the interactive terminal
        scrollback: 1000,
      });

      const fitAddon = new FitAddon();
      term.loadAddon(fitAddon);

      term.open(terminalRef.current);
      fitAddon.fit();

      xtermInstanceRef.current = term;
      fitAddonRef.current = fitAddon;

      // Write initial log
      const initial = useMissionStore.getState();
      term.write(initial.terminalLogs);
      writtenRef.current = initial.terminalBase + initial.terminalLogs.length;
      epochRef.current = initial.terminalEpoch;

      const handleResize = () => {
        try {
          fitAddon.fit();
        } catch {
          // Ignore resize errors when unmounted
        }
      };

      window.addEventListener("resize", handleResize);
    }

    initXterm();

    return () => {
      isMounted = false;
      if (xtermInstanceRef.current) {
        xtermInstanceRef.current.dispose();
        xtermInstanceRef.current = null;
      }
    };
  }, []);

  // Write new log chunks
  useEffect(() => {
    if (!xtermInstanceRef.current) return;
    const term = xtermInstanceRef.current;
    const end = terminalBase + terminalLogs.length;
    // A reset (another session) or output trimmed before it was shown: start over from what is held.
    if (terminalEpoch !== epochRef.current || terminalBase > writtenRef.current) {
      term.reset();
      term.write(terminalLogs);
    } else if (end > writtenRef.current) {
      term.write(terminalLogs.slice(writtenRef.current - terminalBase));
    }
    writtenRef.current = end;
    epochRef.current = terminalEpoch;
  }, [terminalLogs, terminalBase, terminalEpoch]);

  const tabClass = (name: "agent" | "shell") =>
    `px-2 py-0.5 rounded text-xs font-medium cursor-pointer ${
      tab === name ? "bg-cyan/15 text-cyan" : "text-muted-foreground hover:text-foreground"
    }`;

  return (
    <div className="h-full flex flex-col bg-void border-t border-border select-none">
      <div className="h-8 shrink-0 border-b border-border bg-surface px-3 flex items-center gap-2 text-xs text-muted-foreground">
        <TerminalIcon className="w-3.5 h-3.5 text-cyan" />
        <button type="button" data-testid="terminal-tab-agent" className={tabClass("agent")} onClick={() => { setTab("agent"); if (collapsed) onToggle?.(); }}>
          Agent output
        </button>
        <button type="button" data-testid="terminal-tab-shell" className={tabClass("shell")} onClick={() => { setTab("shell"); if (collapsed) onToggle?.(); }}>
          Shell
        </button>
        {onToggle && (
          <button
            type="button"
            data-testid="terminal-toggle"
            aria-label={collapsed ? "Show the terminal" : "Hide the terminal"}
            aria-expanded={!collapsed}
            onClick={onToggle}
            className="ml-auto rounded p-1 hover:bg-elevated hover:text-foreground"
          >
            {collapsed ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
      <div ref={terminalRef} className={`flex-1 p-2 overflow-hidden ${tab === "agent" && !collapsed ? "" : "hidden"}`} />
      <div className={`flex-1 min-h-0 ${tab === "shell" && !collapsed ? "" : "hidden"}`}>
        <InteractiveTerminal active={tab === "shell"} />
      </div>
    </div>
  );
};
