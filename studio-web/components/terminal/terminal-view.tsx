"use client";

import { useEffect, useRef } from "react";
import { useToolStore } from "@/lib/stores/tool-store";
import { apiUrl, websocketUrl } from "@/lib/urls";

interface Props {
  tabId: string;
  toolId: string | null; // null = bash terminal
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
export function TerminalView({ tabId, toolId }: Props) {
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
          toolId ? { type: "tool", tool_id: toolId } : { type: "bash" }
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

      const t = new Terminal({
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        theme: {
          background: "#0a0a0a",
          foreground: "#ededed",
          cursor: "#ededed",
        },
        cursorBlink: true,
        // TUIs (opencode, claude, vim, htop) turn on mouse tracking, which
        // hands every drag to the program and leaves nothing selectable.
        // Shift+drag overrides that everywhere; on macOS Option+drag only
        // does with this set.
        macOptionClickForcesSelection: true,
      });
      const fitAddon = new FitAddon();
      t.loadAddon(fitAddon);
      t.open(containerRef.current!);

      // Terminal-style clipboard keys. Ctrl+C has to stay SIGINT, so on
      // Linux/Windows copy and paste are Ctrl+Shift+C / Ctrl+Shift+V, as in
      // every desktop terminal; without this xterm sent both to the shell as
      // ^C / ^V and there was no way to copy at all. Cmd+C / Cmd+V on macOS
      // already work through the browser's own copy/paste events.
      t.attachCustomKeyEventHandler((ev) => {
        if (ev.type !== "keydown" || !ev.ctrlKey || !ev.shiftKey) return true;
        const key = ev.key.toLowerCase();
        if (key === "c") {
          // preventDefault also stops Chrome's Ctrl+Shift+C (DevTools inspect).
          ev.preventDefault();
          if (t.hasSelection()) copyText(t.getSelection());
          return false;
        }
        if (key === "v") {
          // Let the browser's native paste event through to xterm's textarea
          // (Ctrl+Shift+V is "paste" in Chrome and Firefox). Reading the
          // clipboard ourselves would need a permission prompt and a secure
          // context; this needs neither.
          return false;
        }
        return true;
      });
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

      // Coalesce to one fit per frame. Now that the panel is drag-resizable
      // the observer fires on every pointer move, and each fit that changes
      // the grid sends a resize the server turns into a SIGWINCH — a storm of
      // those makes a full-screen program (vim, htop) flicker as it redraws.
      let fitFrame: number | undefined;
      observer = new ResizeObserver(() => {
        if (fitFrame !== undefined) return;
        fitFrame = window.requestAnimationFrame(() => {
          fitFrame = undefined;
          if (disposed) return;
          fitAddon.fit();
          sendResize();
        });
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

/**
 * Copy to the clipboard. navigator.clipboard exists only in a secure context
 * (https or localhost), and a plain-http deployment is not one, so fall back
 * to execCommand on a throwaway textarea, which still works inside the
 * keydown that triggered it.
 */
function copyText(text: string) {
  const fallback = () => {
    const active = document.activeElement as HTMLElement | null;
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch {
      /* nothing left to try; the selection is still on screen */
    }
    ta.remove();
    active?.focus(); // give the keyboard back to the terminal
  };
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(fallback);
  } else {
    fallback();
  }
}
