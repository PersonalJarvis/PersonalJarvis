/**
 * One named pane of the Agentic IDE: xterm.js wired to
 * `/api/agentic-ide/pty/{name}`, running one coding agent in the chosen folder.
 *
 * Two conventions matter here and both are load-bearing:
 *
 * 1. The xterm instance lives in a ref, never in state — putting it in state
 *    re-renders the component on every output chunk and the pane stutters.
 * 2. Appearance and font size are applied to the LIVE instance in their own
 *    effects. Rebuilding the terminal when the user flips to dark mode would
 *    tear down the WebSocket, which kills the agent running behind it and
 *    loses the whole session. So the connect effect depends on the pane key
 *    alone.
 */
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { AlertCircle, Circle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PANE_CHROME,
  themeFor,
  type TerminalAppearance,
} from "./terminalThemes";

export type PaneStatus = "connecting" | "live" | "exited" | "error";

interface AgenticTerminalProps {
  /** Terminal call-sign — also the WS path segment. */
  name: string;
  /** Agent label shown in the pane header ("Claude Code"). */
  displayName: string;
  appearance: TerminalAppearance;
  fontSize: number;
  /** Highlight this pane as the prompt target. */
  focused?: boolean;
  onFocus?: () => void;
  onStatus?: (status: PaneStatus, detail?: string) => void;
}

function socketUrl(name: string, cols: number, rows: number): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({ cols: String(cols), rows: String(rows) });
  return `${proto}://${window.location.host}/api/agentic-ide/pty/${encodeURIComponent(
    name,
  )}?${query.toString()}`;
}

export function AgenticTerminal({
  name,
  displayName,
  appearance,
  fontSize,
  focused = false,
  onFocus,
  onStatus,
}: AgenticTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const statusRef = useRef<PaneStatus>("connecting");
  // Latest callbacks/appearance without re-running the connect effect.
  const onStatusRef = useRef(onStatus);
  const initialRef = useRef({ appearance, fontSize });
  onStatusRef.current = onStatus;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      convertEol: false,
      fontFamily:
        "'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, 'Courier New', monospace",
      fontSize: initialRef.current.fontSize,
      // Roomier than a console default — the single biggest readability win for
      // an agent that prints prose, diffs and file trees rather than log lines.
      lineHeight: 1.35,
      letterSpacing: 0.2,
      cursorBlink: true,
      cursorStyle: "bar",
      scrollback: 10000,
      theme: themeFor(initialRef.current.appearance),
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(container);
    termRef.current = term;
    fitRef.current = fit;
    try {
      fit.fit();
    } catch {
      /* not measured yet — the ResizeObserver below will fit */
    }

    const report = (status: PaneStatus, detail?: string) => {
      statusRef.current = status;
      onStatusRef.current?.(status, detail);
    };

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

    ws = new WebSocket(socketUrl(name, term.cols || 80, term.rows || 24));
    ws.onopen = () => {
      report("connecting");
      // The spawn used a best-effort size (the mount-time fit usually runs
      // before the grid cell is measured), and resizes sent while the socket
      // was connecting were dropped — without this the agent's full-screen TUI
      // keeps drawing at the wrong width and looks clipped.
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
        report("live");
        term.focus();
      } else if (msg.t === "exit") {
        report("exited", `exit code ${msg.code ?? "?"}`);
        term.write(
          `\r\n\x1b[33m[${displayName} exited — code ${msg.code ?? "?"}]\x1b[0m\r\n`,
        );
      } else if (msg.t === "error") {
        report("error", msg.message ?? "terminal error");
      }
    };
    ws.onerror = () => report("error", "Could not reach the terminal.");
    ws.onclose = (ev) => {
      if (everLive) {
        report("exited");
      } else if (!disposed) {
        report(
          "error",
          ev.code === 4401
            ? "Terminal authorization failed — reopen the pane to retry."
            : ev.code === 4404
              ? "This terminal is no longer part of the open workspace."
              : `Terminal connection closed (code ${ev.code || "?"}).`,
        );
      }
    };

    term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ t: "i", d: data }));
      }
    });

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
      termRef.current = null;
      fitRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see file header:
    // appearance/fontSize must NOT rebuild the pane (it would kill the agent).
  }, [name]);

  // Live restyle — no reconnect, so the running agent is untouched.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.theme = themeFor(appearance);
  }, [appearance]);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.fontSize = fontSize;
    try {
      fitRef.current?.fit();
    } catch {
      /* ignore */
    }
  }, [fontSize]);

  const chrome = PANE_CHROME[appearance];

  return (
    <div
      onMouseDown={onFocus}
      className={cn(
        "relative flex h-full w-full flex-col overflow-hidden rounded-xl border transition-shadow",
        focused
          ? "border-primary/60 shadow-[0_0_0_1px_hsl(var(--primary)/0.35),0_8px_24px_-12px_rgba(0,0,0,0.5)]"
          : "shadow-[0_4px_16px_-10px_rgba(0,0,0,0.4)]",
      )}
      style={{
        background: chrome.shell,
        borderColor: focused ? undefined : chrome.border,
      }}
      data-testid={`agentic-pane-${name}`}
    >
      <PaneHeader
        name={name}
        displayName={displayName}
        appearance={appearance}
        focused={focused}
      />
      <div ref={containerRef} className="flex-1 overflow-hidden px-2 pb-1 pt-1" />
    </div>
  );
}

function PaneHeader({
  name,
  displayName,
  appearance,
  focused,
}: {
  name: string;
  displayName: string;
  appearance: TerminalAppearance;
  focused: boolean;
}) {
  const light = appearance === "light";
  return (
    <header
      className="flex items-center justify-between gap-2 border-b px-3 py-2"
      style={{
        borderColor: PANE_CHROME[appearance].border,
        background: light ? "rgba(0,0,0,0.025)" : "rgba(255,255,255,0.03)",
      }}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={cn(
            "rounded-md px-2 py-0.5 font-display text-[13px] font-semibold tracking-tight",
            focused ? "bg-primary/20 text-primary" : "",
          )}
          style={
            focused
              ? undefined
              : { color: light ? "#2b2b33" : "#e8e8ec", background: light ? "rgba(0,0,0,0.05)" : "rgba(255,255,255,0.06)" }
          }
        >
          {name}
        </span>
        <span
          className="truncate text-[11px] uppercase tracking-wider"
          style={{ color: light ? "#77777f" : "#8a8a95" }}
        >
          {displayName}
        </span>
      </div>
    </header>
  );
}

/** Small status pill reused by the grid toolbar. */
export function PaneStatusPill({ status, detail }: { status: PaneStatus; detail?: string }) {
  if (status === "live") {
    return (
      <span className="flex items-center gap-1 text-[11px] text-emerald-400" title={detail}>
        <Circle className="h-2 w-2 fill-current" />
        live
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="flex items-center gap-1 text-[11px] text-destructive" title={detail}>
        <AlertCircle className="h-3 w-3" />
        error
      </span>
    );
  }
  if (status === "exited") {
    return (
      <span className="text-[11px] text-muted-foreground" title={detail}>
        exited
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[11px] text-muted-foreground" title={detail}>
      <Loader2 className="h-3 w-3 animate-spin" />
      starting
    </span>
  );
}
