"use client";

import { websocketUrl } from "@/lib/urls";
import { useEffect, useRef } from "react";

interface Props {
  tabId: string;
  toolId: string | null; // null = bash terminal
}

/**
 * Renders an xterm.js terminal connected via WebSocket to the backend PTY.
 *
 * The WebSocket URL is:
 * - ws/terminal          for bash
 * - ws/terminal/{toolId} for AI tools
 */
export function TerminalView({ tabId, toolId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current || !containerRef.current) return;
    initialized.current = true;

    let ws: WebSocket | null = null;
    let terminal: unknown = null;

    async function init() {
      // Dynamic import to avoid SSR issues
      const { Terminal } = await import("@xterm/xterm");
      const { FitAddon } = await import("@xterm/addon-fit");

      const term = new Terminal({
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        theme: {
          background: "#0a0a0a",
          foreground: "#ededed",
          cursor: "#ededed",
        },
        cursorBlink: true,
      });

      const fitAddon = new FitAddon();
      term.loadAddon(fitAddon);
      term.open(containerRef.current!);
      fitAddon.fit();
      terminal = term;

      // Connect WebSocket
      const wsPath = toolId
        ? `/terminal/${toolId}`
        : "/terminal";
      ws = new WebSocket(websocketUrl(wsPath));

      ws.onopen = () => {
        // Send initial resize
        const msg = JSON.stringify({
          type: "resize",
          cols: term.cols,
          rows: term.rows,
        });
        ws!.send(msg);
      };

      ws.onmessage = (event) => {
        term.write(event.data);
      };

      ws.onclose = () => {
        term.write("\r\n\x1b[33m[Session ended]\x1b[0m\r\n");
      };

      // Terminal → WebSocket
      term.onData((data) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(data);
        }
      });

      // Handle resize
      const observer = new ResizeObserver(() => {
        fitAddon.fit();
        if (ws && ws.readyState === WebSocket.OPEN) {
          const msg = JSON.stringify({
            type: "resize",
            cols: term.cols,
            rows: term.rows,
          });
          ws.send(msg);
        }
      });
      observer.observe(containerRef.current!);

      return () => observer.disconnect();
    }

    init();

    return () => {
      if (ws) ws.close();
      if (terminal && typeof (terminal as { dispose: () => void }).dispose === "function") {
        (terminal as { dispose: () => void }).dispose();
      }
    };
  }, [tabId, toolId]);

  return (
    <div
      ref={containerRef}
      className="h-full w-full bg-[#0a0a0a]"
    />
  );
}
