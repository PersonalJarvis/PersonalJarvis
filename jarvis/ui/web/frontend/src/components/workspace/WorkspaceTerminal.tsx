/**
 * One embedded, interactive agent terminal — xterm.js wired bidirectionally to
 * the workspace PTY WebSocket (`/api/workspace/pty/{key}`). The agent (Claude
 * Code / Codex) runs in a real PTY in the Jarvis project folder; keystrokes go
 * up as `{t:"i"}`, output comes down as `{t:"o"}`, resizes as `{t:"r"}`.
 *
 * Conventions copied from PtyTerminal: xterm instance in a ref (never state, or
 * it rerenders per chunk), dispose() on unmount (WebGL context cap).
 */
import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { Terminal as TerminalIcon, AlertCircle } from "lucide-react";

type Status = "connecting" | "live" | "exited" | "error";

interface WorkspaceTerminalProps {
  /** A stable key for this pane (used only as the WS path segment). */
  paneKey: string;
  /** Run an agent by name (e.g. "claude"); mutually exclusive with installName. */
  agentName?: string;
  /** Run an agent's installer by name; mutually exclusive with agentName. */
  installName?: string;
  /** Header label shown above the terminal. */
  title: string;
}

async function getToken(): Promise<string> {
  const injected = (window as unknown as { __JARVIS_TOKEN?: string }).__JARVIS_TOKEN;
  if (injected) return injected;
  try {
    const res = await fetch("/api/missions/auth/token");
    const body = (await res.json()) as { token?: string };
    return body.token ?? "";
  } catch {
    return "";
  }
}

function buildUrl(paneKey: string, params: Record<string, string>): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams(params).toString();
  return `${proto}://${window.location.host}/api/workspace/pty/${encodeURIComponent(
    paneKey,
  )}?${query}`;
}

export function WorkspaceTerminal({
  paneKey,
  agentName,
  installName,
  title,
}: WorkspaceTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      convertEol: false,
      fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, 'Courier New', monospace",
      fontSize: 12,
      lineHeight: 1.15,
      cursorBlink: true,
      scrollback: 5000,
      theme: {
        background: "#0b0d10",
        foreground: "#e6e6e6",
        cursor: "#ffd60a",
        selectionBackground: "#3a4252",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(container);
    try {
      fit.fit();
    } catch {
      /* container not measured yet — ResizeObserver will fit shortly */
    }

    let ws: WebSocket | null = null;
    let disposed = false;
    let everLive = false;

    const sendResize = () => {
      try {
        fit.fit();
      } catch {
        return;
      }
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ t: "r", cols: term.cols, rows: term.rows }));
      }
    };

    void (async () => {
      const token = await getToken();
      if (disposed) return;
      const params: Record<string, string> = {
        token,
        cols: String(term.cols || 80),
        rows: String(term.rows || 24),
      };
      if (agentName) params.agent = agentName;
      else if (installName) params.install = installName;

      ws = new WebSocket(buildUrl(paneKey, params));
      ws.onopen = () => {
        setStatus("connecting");
        // Push the ACTUAL pane size to the PTY now that we can send. The spawn
        // used a best-effort size (the mount-time fit often runs before the
        // grid cell is measured), and resizes fired while the socket was still
        // connecting were dropped — so without this the agent's full-screen TUI
        // keeps drawing at the wrong dimensions (cramped / clipped on the
        // right). A second deferred fit catches any late grid layout.
        sendResize();
        requestAnimationFrame(sendResize);
      };
      ws.onmessage = (ev) => {
        let msg: { t?: string; d?: string; code?: number; message?: string };
        try {
          msg = JSON.parse(ev.data as string);
        } catch {
          return;
        }
        if (msg.t === "o") term.write(msg.d ?? "");
        else if (msg.t === "ready") {
          everLive = true;
          setStatus("live");
          term.focus();
        } else if (msg.t === "exit") {
          setStatus("exited");
          term.write(`\r\n\x1b[33m[process exited: ${msg.code ?? "?"}]\x1b[0m\r\n`);
        } else if (msg.t === "error") {
          setStatus("error");
          setError(msg.message ?? "terminal error");
        }
      };
      ws.onerror = () => {
        setStatus("error");
        setError("Connection to the terminal failed.");
      };
      ws.onclose = (ev) => {
        if (everLive) {
          setStatus("exited");
        } else if (!disposed) {
          // Closed during the handshake (e.g. 4401 auth) — surface it honestly
          // instead of hanging on "connecting" forever (mirrors PtyTerminal).
          setStatus("error");
          setError((e) =>
            e ??
            (ev.code === 4401
              ? "Terminal authorization failed — reopen to retry."
              : `Terminal connection closed (code ${ev.code || "?"}).`),
          );
        }
      };

      term.onData((data) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ t: "i", d: data }));
        }
      });
    })();

    window.addEventListener("resize", sendResize);
    const ro = new ResizeObserver(() => sendResize());
    ro.observe(container);

    return () => {
      disposed = true;
      window.removeEventListener("resize", sendResize);
      ro.disconnect();
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
      term.dispose();
    };
  }, [paneKey, agentName, installName]);

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden rounded-lg border border-border bg-[#0b0d10]">
      <header className="flex items-center justify-between gap-2 border-b border-border bg-card/40 px-3 py-1.5">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <TerminalIcon className="h-3.5 w-3.5 text-primary" />
          <span className="font-mono">{title}</span>
        </div>
        <span className="text-[10px] uppercase tracking-wider">
          {status === "live" ? (
            <span className="text-emerald-400">live</span>
          ) : status === "error" ? (
            <span className="flex items-center gap-1 text-destructive">
              <AlertCircle className="h-3 w-3" />
              error
            </span>
          ) : status === "exited" ? (
            <span className="text-muted-foreground">exited</span>
          ) : (
            <span className="text-muted-foreground">connecting…</span>
          )}
        </span>
      </header>
      <div ref={containerRef} className="flex-1 overflow-hidden p-1" />
      {error && (
        <div className="border-t border-border bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">
          {error}
        </div>
      )}
    </div>
  );
}
