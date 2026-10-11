"use client";

// Bundled with the app (not a CDN link) so the terminal works offline.
import "@xterm/xterm/css/xterm.css";

import React, { useEffect, useRef, useState } from "react";
import type { Terminal as XtermType } from "@xterm/xterm";
import type { FitAddon as FitAddonType } from "@xterm/addon-fit";
import { useMissionStore } from "../lib/store";
import { InteractiveTerminal } from "./InteractiveTerminal";
import { Terminal as TerminalIcon } from "lucide-react";

export const StreamingTerminal: React.FC = () => {
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermInstanceRef = useRef<XtermType | null>(null);
  const fitAddonRef = useRef<FitAddonType | null>(null);
  const { terminalLogs } = useMissionStore();
  const [tab, setTab] = useState<"agent" | "shell">("agent");
  const lastWrittenLengthRef = useRef<number>(0);

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
      const initialLogs = useMissionStore.getState().terminalLogs;
      term.write(initialLogs);
      lastWrittenLengthRef.current = initialLogs.length;

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
    if (terminalLogs.length > lastWrittenLengthRef.current) {
      const newChunk = terminalLogs.substring(lastWrittenLengthRef.current);
      xtermInstanceRef.current.write(newChunk);
      lastWrittenLengthRef.current = terminalLogs.length;
    }
  }, [terminalLogs]);

  const tabClass = (name: "agent" | "shell") =>
    `px-2 py-0.5 rounded font-bold tracking-wider cursor-pointer ${
      tab === name ? "bg-cyan/15 text-cyan" : "text-muted-foreground hover:text-foreground"
    }`;

  return (
    <div className="h-full flex flex-col bg-void border-t border-border select-none">
      <div className="h-8 border-b border-border bg-surface px-3 flex items-center gap-2 text-xs font-mono text-muted-foreground">
        <TerminalIcon className="w-3.5 h-3.5 text-cyan" />
        <button type="button" className={tabClass("agent")} onClick={() => setTab("agent")}>
          AGENT OUTPUT
        </button>
        <button type="button" className={tabClass("shell")} onClick={() => setTab("shell")}>
          SHELL
        </button>
      </div>
      <div ref={terminalRef} className={`flex-1 p-2 overflow-hidden ${tab === "agent" ? "" : "hidden"}`} />
      <div className={`flex-1 min-h-0 ${tab === "shell" ? "" : "hidden"}`}>
        <InteractiveTerminal active={tab === "shell"} />
      </div>
    </div>
  );
};
