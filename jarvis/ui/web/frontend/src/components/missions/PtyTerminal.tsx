/**
 * Live-Terminal fuer einen Worker. xterm.js + FitAddon + Backpressure.
 *
 * Zwingende Conventions:
 *  - Terminal-Instance ueber useRef (NICHT useState) — sonst rerender pro chunk.
 *  - dispose() im Cleanup: ohne das laeuft jede Worker-Selection in einen
 *    WebGL-Context-Leak (Chrome cap = 16 Contexts pro Origin).
 *  - Backpressure: bytesPending wird lokal gezaehlt; ab 128 KB pending wird ein
 *    `{type:"pause"}` an den PTY-Stream gesendet, ab 16 KB wieder `resume`.
 *
 * MVP-Hinweis: das Backend-PTY-Endpoint ist als Stub geplant (Phase 6, separater
 * Sub-Agent). Bis es fertig ist, zeigt diese Component einen "Stream nicht
 * verfuegbar"-Placeholder, sobald der WS-Connect failt.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { SearchAddon } from "@xterm/addon-search";
import "@xterm/xterm/css/xterm.css";
import { AlertCircle, Terminal as TerminalIcon } from "lucide-react";
import {
  disposeTerminal,
  getTerminal,
  setTerminal,
} from "./terminalRegistry";

const PAUSE_THRESHOLD = 128 * 1024;
const RESUME_THRESHOLD = 16 * 1024;

interface PtyTerminalProps {
  workerId: string;
}

function buildPtyUrl(workerId: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  const token = window.__JARVIS_TOKEN;
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}://${host}/api/missions/pty/${encodeURIComponent(workerId)}${query}`;
}

export function PtyTerminal({ workerId }: PtyTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const bytesPendingRef = useRef(0);
  const pausedRef = useRef(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const sendControl = useCallback(
    (type: "pause" | "resume") => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type, worker_id: workerId }));
        } catch {
          // ignore — Backpressure-Hinweis ist best-effort
        }
      }
    },
    [workerId],
  );

  useEffect(() => {
    if (!containerRef.current) return;

    let term = getTerminal(workerId);
    if (!term) {
      term = new Terminal({
        convertEol: true,
        fontFamily:
          "'JetBrains Mono', 'Fira Code', Consolas, 'Courier New', monospace",
        fontSize: 12,
        lineHeight: 1.2,
        cursorBlink: false,
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
      term.loadAddon(new SearchAddon());
      term.open(containerRef.current);
      fit.fit();
      setTerminal(workerId, term);
      termRef.current = term;
      fitRef.current = fit;
    } else {
      term.open(containerRef.current);
      const fit = new FitAddon();
      term.loadAddon(fit);
      fit.fit();
      termRef.current = term;
      fitRef.current = fit;
    }

    const handleResize = () => {
      try {
        fitRef.current?.fit();
      } catch {
        // resize-failures sind nicht kritisch
      }
    };
    window.addEventListener("resize", handleResize);

    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(buildPtyUrl(workerId));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        setConnected(true);
        setStreamError(null);
      });

      ws.addEventListener("message", (ev) => {
        const term = termRef.current;
        if (!term) return;
        let data: string;
        let size = 0;
        if (typeof ev.data === "string") {
          data = ev.data;
          size = data.length;
        } else if (ev.data instanceof ArrayBuffer) {
          const decoder = new TextDecoder();
          data = decoder.decode(ev.data);
          size = ev.data.byteLength;
        } else {
          return;
        }

        bytesPendingRef.current += size;
        if (
          !pausedRef.current &&
          bytesPendingRef.current > PAUSE_THRESHOLD
        ) {
          pausedRef.current = true;
          sendControl("pause");
        }

        term.write(data, () => {
          bytesPendingRef.current = Math.max(
            0,
            bytesPendingRef.current - size,
          );
          if (
            pausedRef.current &&
            bytesPendingRef.current < RESUME_THRESHOLD
          ) {
            pausedRef.current = false;
            sendControl("resume");
          }
        });
      });

      ws.addEventListener("error", () => {
        setStreamError("PTY-Stream nicht erreichbar.");
      });

      ws.addEventListener("close", (ev) => {
        setConnected(false);
        if (ev.code !== 1000 && ev.code !== 1001) {
          setStreamError(`Stream getrennt (Code ${ev.code}).`);
        }
      });
    } catch (e) {
      setStreamError(`PTY-Connect fehlgeschlagen: ${(e as Error).message}`);
    }

    return () => {
      window.removeEventListener("resize", handleResize);
      try {
        wsRef.current?.close(1000, "unmount");
      } catch {
        // ignore
      }
      wsRef.current = null;
      // Terminal wird disposed wenn der WORKER waechselt (siehe key-prop in
      // MissionsView), nicht bei jedem Re-Render
      disposeTerminal(workerId);
      termRef.current = null;
      fitRef.current = null;
    };
  }, [workerId, sendControl]);

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden rounded-md border border-border bg-[#0b0d10]">
      <header className="flex items-center justify-between gap-2 border-b border-border bg-card/40 px-3 py-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <TerminalIcon className="h-3.5 w-3.5 text-primary" />
          <span className="font-mono">worker {workerId.slice(0, 12)}</span>
        </div>
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider">
          {streamError ? (
            <span className="flex items-center gap-1 text-destructive">
              <AlertCircle className="h-3 w-3" />
              offline
            </span>
          ) : connected ? (
            <span className="text-emerald-400">live</span>
          ) : (
            <span className="text-muted-foreground">verbinde…</span>
          )}
        </div>
      </header>
      <div ref={containerRef} className="flex-1 overflow-hidden p-1" />
      {streamError && (
        <div className="border-t border-border bg-destructive/10 px-3 py-2 text-[11px] text-destructive">
          {streamError}
        </div>
      )}
    </div>
  );
}
