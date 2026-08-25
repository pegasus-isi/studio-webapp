"use client";

import { useEffect, useRef } from "react";
import { useToolStore } from "@/lib/stores/tool-store";
import { apiUrl, websocketUrl } from "@/lib/urls";

interface Props {
  tabId: string;
  toolId: string | null; // null = bash terminal
  // Open the shell on the remote compute backend's submit host.
  remote?: boolean;
}

/**
 * xterm.js terminal attached to a server-held PTY session.
 *
 * The backend owns the shell (services/terminal_sessions.py): this component
 * resolves the tab's backend session (reusing the persisted session id after
 * a reload, creating one otherwise), attaches via /ws/terminals/{id} — which
 * replays the scrollback — and auto-reconnects after unexpected closes.
 * URLs go through apiUrl()/websocketUrl() so they resolve under the per-user
 * base path. Unmounting only detaches; the shell keeps running.
 */
export function TerminalView({ tabId, toolId, remote }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current || !containerRef.current) return;
    initialized.current = true;

    let ws: WebSocket | null = null;
    let term: import("@xterm/xterm").Terminal | null = null;
    let observer: ResizeObserver | null = null;
    let reconnectTimer: number | undefined;
    let disposed = false;

    /** Reuse the tab's stored backend session if it's still alive; else create. */
    async function resolveSession(): Promise<string> {
      const stored = useToolStore
        .getState()
        .tabs.find((t) => t.id === tabId)?.sessionId;
      if (stored) {
        try {
          const res = await fetch(apiUrl("terminals"));
          if (res.ok) {
            const list: { id: string }[] = await res.json();
            if (list.some((s) => s.id === stored)) return stored;
          }
        } catch {
          /* fall through to create */
        }
        useToolStore.getState().setTabSession(tabId, undefined);
      }
      const res = await fetch(apiUrl("terminals"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          toolId
            ? { type: "tool", tool_id: toolId }
            : { type: "bash", remote: !!remote }
        ),
      });
      if (!res.ok) throw new Error(`create failed: ${res.status}`);
      const meta: { id: string } = await res.json();
      useToolStore.getState().setTabSession(tabId, meta.id);
      return meta.id;
    }

    async function init() {
      const { Terminal } = await import("@xterm/xterm");
      const { FitAddon } = await import("@xterm/addon-fit");

      // The cleanup may have already run while those imports were in flight
      // (StrictMode's dev mount/unmount does this on every mount): `term` was
      // still null then, so its dispose() had nothing to kill — continuing
      // here would append an orphan xterm the cleanup can never reach. That
      // zombie stacked above the remount's live terminal, pushing the real
      // prompt to the container's bottom edge and leaving a dead cursor in
      // the top-left (FE-8).
      if (disposed || !containerRef.current) return;

      const t = new Terminal({
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
      t.loadAddon(fitAddon);
      t.open(containerRef.current!);
      fitAddon.fit();
      term = t;

      const sendResize = () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(
            JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows })
          );
        }
      };

      const connect = async () => {
        if (disposed) return;
        let sessionId: string;
        try {
          sessionId = await resolveSession();
        } catch {
          t.write("\r\n\x1b[31m[Could not create terminal session]\x1b[0m\r\n");
          return;
        }
        ws = new WebSocket(websocketUrl(`terminals/${sessionId}`));
        ws.onopen = () => {
          // The server replays the scrollback as the first message(s);
          // start from a clean screen so nothing duplicates.
          t.reset();
          sendResize();
        };
        ws.onmessage = (event) => t.write(event.data);
        ws.onclose = (event) => {
          if (disposed) return;
          if (event.code === 1000 || event.code === 4004) {
            useToolStore.getState().setTabSession(tabId, undefined);
            t.write("\r\n\x1b[33m[Session ended]\x1b[0m\r\n");
          } else {
            t.write("\r\n\x1b[33m[Disconnected — reconnecting…]\x1b[0m\r\n");
            reconnectTimer = window.setTimeout(connect, 2000);
          }
        };
      };

      t.onData((data) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(data);
        }
      });

      observer = new ResizeObserver(() => {
        // A hidden tab (display:none) reports a zero-size container. Fitting
        // then shrinks the PTY to a couple of columns and the shell's
        // SIGWINCH redraw rewraps the current line at that width — leaving
        // permanently truncated prompt fragments ("ubuntu@su…") in the
        // scrollback after every tab switch. Only refit while visible; the
        // observer fires again with real dimensions when the tab reappears.
        const el = containerRef.current;
        if (!el || el.clientWidth === 0 || el.clientHeight === 0) return;
        fitAddon.fit();
        sendResize();
      });
      observer.observe(containerRef.current!);

      await connect();
    }

    init();

    return () => {
      // Detach only — the server-held shell keeps running for reattach.
      disposed = true;
      // Reset the init guard so a StrictMode mount/unmount/remount (dev) — or
      // any legitimate remount — re-initializes instead of leaving a dead
      // black div and a leaked, undisposed xterm (FE-8).
      initialized.current = false;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      observer?.disconnect();
      ws?.close();
      term?.dispose();
    };
  }, [tabId, toolId]);

  return <div ref={containerRef} className="h-full w-full bg-[#0a0a0a]" />;
}
