"use client";

import "@xterm/xterm/css/xterm.css";

import React, { useCallback, useEffect, useRef, useState } from "react";
import type { Terminal as XtermType } from "@xterm/xterm";
import type { FitAddon as FitAddonType } from "@xterm/addon-fit";
import { getActiveMissionWebSocket, getDefaultWsBaseUrl } from "../lib/websocket";

type Status = "idle" | "connecting" | "connected" | "closed";

/**
 * A real shell in the session's sandbox: keystrokes go to the container's PTY and its output
 * comes back byte for byte (binary frames), so vim, htop, tab completion and ctrl-c work.
 * Control (resize) travels as JSON text frames. It only connects once the tab is first shown.
 */
export const InteractiveTerminal: React.FC<{ active: boolean }> = ({ active }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XtermType | null>(null);
  const fitRef = useRef<FitAddonType | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);
  // xterm is loaded lazily; connecting has to wait for it however long the chunk takes.
  const [terminalReady, setTerminalReady] = useState(false);

  const open = useCallback(() => {
    const term = termRef.current;
    const live = getActiveMissionWebSocket();
    if (!term || !live) {
      setMessage("Send a message to start a session, then open the shell.");
      return;
    }
    socketRef.current?.close();
    term.reset();
    setMessage(null);
    setStatus("connecting");

    const query = new URLSearchParams({ cols: String(term.cols), rows: String(term.rows) });
    if (live.getToken()) query.set("token", live.getToken());
    const socket = new WebSocket(
      `${getDefaultWsBaseUrl()}/v1/sessions/${live.getSessionId()}/terminal?${query}`
    );
    socket.binaryType = "arraybuffer";
    socketRef.current = socket;

    socket.onopen = () => setStatus("connected");
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        const frame = JSON.parse(event.data) as { type?: string; detail?: string; reason?: string };
        if (frame.type === "error") setMessage(frame.detail ?? "The terminal could not be opened.");
        if (frame.type === "exit") setMessage(`Terminal closed (${frame.reason ?? "ended"}).`);
        return;
      }
      term.write(new Uint8Array(event.data as ArrayBuffer));
    };
    socket.onclose = () => setStatus("closed");
    socket.onerror = () => setMessage((current) => current ?? "Could not reach the terminal.");
  }, []);

  useEffect(() => {
    let mounted = true;
    let observer: ResizeObserver | null = null;

    async function init() {
      if (!containerRef.current || termRef.current) return;
      const { Terminal } = await import("@xterm/xterm");
      const { FitAddon } = await import("@xterm/addon-fit");
      if (!mounted || !containerRef.current) return;

      const term = new Terminal({
        theme: { background: "#08090D", foreground: "#F8FAFC", cursor: "#FF4D00" },
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 12,
        cursorBlink: true,
        scrollback: 5000,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(containerRef.current);
      fit.fit();
      termRef.current = term;
      fitRef.current = fit;
      setTerminalReady(true);

      const encoder = new TextEncoder();
      term.onData((data) => {
        const socket = socketRef.current;
        if (socket && socket.readyState === WebSocket.OPEN) socket.send(encoder.encode(data));
      });
      term.onResize(({ cols, rows }) => {
        const socket = socketRef.current;
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "resize", cols, rows }));
        }
      });
      observer = new ResizeObserver(() => {
        try {
          fit.fit();
        } catch {
          /* hidden or unmounted */
        }
      });
      observer.observe(containerRef.current);
    }
    init();

    return () => {
      mounted = false;
      observer?.disconnect();
      socketRef.current?.close();
      termRef.current?.dispose();
      termRef.current = null;
    };
  }, []);

  // Connect lazily, the first time the tab is shown.
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => {
      try {
        fitRef.current?.fit();
      } catch {
        /* not laid out yet */
      }
      if (status === "idle" && termRef.current) open();
    }, 50);
    return () => clearTimeout(timer);
  }, [active, status, open, terminalReady]);

  return (
    <div className="h-full flex flex-col" data-testid="interactive-terminal">
      {(message || status === "closed") && (
        <div className="px-3 py-1.5 text-[11px] font-mono text-amber-400 bg-surface flex items-center justify-between">
          <span>{message ?? "Disconnected."}</span>
          <button type="button" onClick={open} className="underline hover:text-amber-300">
            Reconnect
          </button>
        </div>
      )}
      <div ref={containerRef} className="flex-1 p-2 overflow-hidden" />
    </div>
  );
};
