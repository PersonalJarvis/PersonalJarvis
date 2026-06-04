import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { TerminalSquare, Play, Square, RefreshCw } from "lucide-react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { getWSClient } from "@/hooks/useWebSocket";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { CliConnectCoach } from "@/components/CliConnectCoach";
import { useT } from "@/i18n";

interface ShellInfo {
  id: string;
  label: string;
}

type Status = "idle" | "starting" | "running" | "closed";

const TERMINAL_THEME = {
  background: "#0a0a0a",
  foreground: "#e5e5e5",
  cursor: "#ffd60a",
  cursorAccent: "#0a0a0a",
  selectionBackground: "rgba(255, 214, 10, 0.25)",
  black: "#1a1a1a",
  red: "#f87171",
  green: "#4ade80",
  yellow: "#facc15",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#22d3ee",
  white: "#e5e5e5",
  brightBlack: "#525252",
  brightRed: "#fca5a5",
  brightGreen: "#86efac",
  brightYellow: "#fde047",
  brightBlue: "#93c5fd",
  brightMagenta: "#d8b4fe",
  brightCyan: "#67e8f9",
  brightWhite: "#fafafa",
} as const;

export function TerminalView() {
  const t = useT();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const terminalIdRef = useRef<string | null>(null);
  // Status-Ref weil der WS-Subscriber-Closure beim ersten Render eingefroren
  // wird und sonst die "starting"-Zuordnung nicht erkennt.
  const statusRef = useRef<Status>("idle");
  // Auto-Input, das nach dem naechsten Spawn-Success geschrieben wird (z.B.
  // ein CLI-Install-Command, den der User aus ClisView getriggert hat).
  const pendingInputRef = useRef<string | null>(null);

  const [shells, setShells] = useState<ShellInfo[]>([]);
  const [selectedShell, setSelectedShell] = useState<string>("");
  const [status, setStatus] = useState<Status>("idle");
  const [exitCode, setExitCode] = useState<number | null>(null);

  const pendingTerminalCommand = useEventStore((s) => s.pendingTerminalCommand);
  const setPendingTerminalCommand = useEventStore(
    (s) => s.setPendingTerminalCommand,
  );
  const cliConnectCoach = useEventStore((s) => s.cliConnectCoach);
  const pushToast = useEventStore((s) => s.pushToast);
  const qc = useQueryClient();

  statusRef.current = status;

  // Shells einmalig vom Backend holen.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/terminal/shells")
      .then((r) => r.json())
      .then((data: { shells?: ShellInfo[] }) => {
        if (cancelled) return;
        const list = data.shells ?? [];
        setShells(list);
        if (list.length > 0) setSelectedShell(list[0].id);
      })
      .catch(() => {
        if (!cancelled) setShells([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // xterm-Instanz erstellen — laeuft genau einmal beim Mount.
  useEffect(() => {
    if (!containerRef.current) return;

    const term = new XTerm({
      fontFamily:
        '"JetBrains Mono", "Fira Code", "Cascadia Code", Menlo, Consolas, ui-monospace, monospace',
      fontSize: 13,
      lineHeight: 1.2,
      cursorBlink: true,
      cursorStyle: "bar",
      theme: TERMINAL_THEME,
      allowProposedApi: true,
      scrollback: 5000,
      convertEol: false,
    });
    const fit = new FitAddon();
    const links = new WebLinksAddon();
    term.loadAddon(fit);
    term.loadAddon(links);
    term.open(containerRef.current);
    try {
      fit.fit();
    } catch {
      // beim ersten Render kann ContainerSize 0 sein
    }
    termRef.current = term;
    fitRef.current = fit;

    const resizeObs = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // ignore — container temporaer hidden
      }
    });
    resizeObs.observe(containerRef.current);

    return () => {
      resizeObs.disconnect();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // xterm → WS: User-Input + Resize.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;

    const inputDisposer = term.onData((data) => {
      const id = terminalIdRef.current;
      if (!id) return;
      getWSClient()?.send({
        type: "command",
        action: "terminal.input",
        payload: { terminal_id: id, data },
      });
    });

    const resizeDisposer = term.onResize(({ cols, rows }) => {
      const id = terminalIdRef.current;
      if (!id) return;
      getWSClient()?.send({
        type: "command",
        action: "terminal.resize",
        payload: { terminal_id: id, cols, rows },
      });
    });

    return () => {
      inputDisposer.dispose();
      resizeDisposer.dispose();
    };
  }, []);

  // WS → xterm: terminal.spawned, TerminalOutput, TerminalClosed.
  useEffect(() => {
    const client = getWSClient();
    if (!client) return;

    const unsub = client.subscribe((raw) => {
      if (typeof raw !== "object" || raw === null) return;
      const frame = raw as {
        type?: string;
        event_name?: string;
        payload?: Record<string, unknown>;
      };

      // Direktes Antwort-Frame zur Spawn-Anfrage.
      if (frame.type === "terminal.spawned" && statusRef.current === "starting") {
        const p = frame.payload ?? {};
        const id = typeof p.terminal_id === "string" ? p.terminal_id : null;
        if (id) {
          terminalIdRef.current = id;
          setStatus("running");
          setExitCode(null);
          // Initiale Groesse melden — dimensions koennen sich seit spawn aendern.
          const term = termRef.current;
          if (term) {
            client.send({
              type: "command",
              action: "terminal.resize",
              payload: { terminal_id: id, cols: term.cols, rows: term.rows },
            });
          }
          // Pending Auto-Input einspielen (z.B. ein CLI-Install-Command).
          // Wir haengen "\r" an, damit die Shell den Command direkt ausfuehrt.
          const pending = pendingInputRef.current;
          if (pending) {
            pendingInputRef.current = null;
            // Kleine Verzoegerung, damit die Shell ihren Prompt rendern kann
            // bevor wir schreiben — sonst frisst PowerShell die ersten Zeichen.
            setTimeout(() => {
              client.send({
                type: "command",
                action: "terminal.input",
                payload: { terminal_id: id, data: pending + "\r" },
              });
            }, 250);
          }
        }
        return;
      }

      // Bus-Events.
      if (frame.type !== "event") return;
      const payload = frame.payload ?? {};
      const myId = terminalIdRef.current;

      if (frame.event_name === "TerminalOutput") {
        const data = payload.data;
        const tid = payload.terminal_id;
        if (typeof data === "string" && tid === myId) {
          termRef.current?.write(data);
        }
      } else if (frame.event_name === "TerminalClosed") {
        const tid = payload.terminal_id;
        if (tid === myId) {
          const code = typeof payload.exit_code === "number" ? payload.exit_code : -1;
          setStatus("closed");
          setExitCode(code);
          terminalIdRef.current = null;
          termRef.current?.write(
            `\r\n\x1b[33m[Session ended · exit ${code}]\x1b[0m\r\n`,
          );
          // Auto-Verify nach Install: wenn der User aus ClisView heraus
          // einen Install gestartet hat, triggern wir nach exit_code=0
          // automatisch einen /check und ein Toast — damit der Status in
          // der CLIs-View sofort von "nicht installiert" auf "installiert"
          // springt, ohne dass der User irgendwo klickt.
          const cliName = useEventStore.getState().pendingInstallCliName;
          if (cliName && code === 0) {
            useEventStore.getState().setPendingInstallCliName(null);
            // Combined-Flow: Verify → ggf. Auto-Login → Coach starten.
            // Der User soll nach einem Klick "Installieren" nicht mehr
            // manuell auf "Verbinden" klicken muessen — wir verketten alles.
            void (async () => {
              try {
                const r = await fetch(`/api/clis/${cliName}/check`, {
                  method: "POST",
                });
                if (!r.ok) return;
                const check = await r.json() as {
                  installed?: boolean;
                  connected?: boolean;
                  version?: string | null;
                };
                qc.invalidateQueries({ queryKey: ["clis"] });
                qc.invalidateQueries({ queryKey: ["cli", cliName] });

                if (!check.installed) {
                  pushToast(
                    "warning",
                    t("terminal_xterm.install_no_binary").replace("{0}", cliName),
                  );
                  return;
                }

                const v = check.version ? ` (${check.version})` : "";
                pushToast("success", `${cliName} installed${v}`);

                if (check.connected) return; // Schon verbunden — fertig.

                // Auto-Login: hol Detail + ConnectConfig parallel und
                // spawne den Login-Command in einer neuen Terminal-Session.
                const [detailR, cfgR] = await Promise.all([
                  fetch(`/api/clis/${cliName}`),
                  fetch(`/api/clis/${cliName}/connect-config`),
                ]);
                if (!detailR.ok || !cfgR.ok) return;
                const detail = await detailR.json() as {
                  display_name?: string;
                  auth_mode?: string;
                  status_command?: string | null;
                };
                const cfg = await cfgR.json() as {
                  login_command?: string | null;
                };
                if (detail.auth_mode === "none" || !cfg.login_command) {
                  // Keine Login-Phase noetig → Status springt eh schon
                  // automatisch auf "verbunden" (auth_mode=none gilt als
                  // connected sobald installiert). Fertig.
                  return;
                }

                // KEIN Banner hier schreiben — der Auto-Spawn-Effect macht
                // term.clear() vor dem neuen Spawn und setzt seinen eigenen
                // Banner aus pendingTerminalCommand.label. Doppelter Banner
                // = einer wird sofort gecleared, sieht buggy aus.

                // Coach setzen (Polling startet sobald Spawn ankommt)
                useEventStore.getState().setCliConnectCoach({
                  cliName,
                  displayName: detail.display_name || cliName,
                  authMode: (detail.auth_mode as "oauth_cli" | "api_key" | "config_file" | "none") || "oauth_cli",
                  loginCommand: cfg.login_command,
                  statusCommand: detail.status_command || null,
                });

                // Pending-Command setzen + Status auf idle damit der
                // Auto-Spawn-Effect eine neue Session startet.
                useEventStore.getState().setPendingTerminalCommand({
                  command: cfg.login_command,
                  shell: "pwsh",
                  label: `${detail.display_name || cliName} · Login (auto)`,
                });
                setStatus("idle");
              } catch {
                // Silent — Status-Refresh ist nice-to-have, kein Hard-Fail.
              }
            })();
          } else if (cliName && code !== 0) {
            // Install-Failure → User informieren + Marker zuruecksetzen.
            useEventStore.getState().setPendingInstallCliName(null);
            pushToast(
              "error",
              t("terminal_xterm.install_failed").replace("{0}", cliName).replace("{1}", String(code)),
            );
          }
        }
      }
    });

    return () => {
      unsub();
    };
  }, []);

  // Beim Unmount der View: aktive PTY-Session schliessen.
  useEffect(() => {
    return () => {
      const id = terminalIdRef.current;
      if (id) {
        getWSClient()?.send({
          type: "command",
          action: "terminal.close",
          payload: { terminal_id: id },
        });
        terminalIdRef.current = null;
      }
    };
  }, []);

  const start = (shellOverride?: string) => {
    const shellId = shellOverride ?? selectedShell;
    if (!shellId) return;
    const term = termRef.current;
    const fit = fitRef.current;
    if (!term || !fit) return;

    // Falls noch eine Session laeuft, erst sauber schliessen.
    const prev = terminalIdRef.current;
    if (prev) {
      getWSClient()?.send({
        type: "command",
        action: "terminal.close",
        payload: { terminal_id: prev },
      });
      terminalIdRef.current = null;
    }

    term.clear();
    try {
      fit.fit();
    } catch {
      // ignore
    }
    setStatus("starting");
    setExitCode(null);
    getWSClient()?.send({
      type: "command",
      action: "terminal.spawn",
      payload: {
        shell: shellId,
        cols: term.cols,
        rows: term.rows,
      },
    });
  };

  // Auto-Start bei Pending-Command (z.B. CLI-Install aus ClisView). Sobald die
  // Shells geladen sind, waehlen wir die passende Shell, setzen den Command
  // als Auto-Input und spawnen die PTY. WS-Subscriber oben spielt den Command
  // ein, sobald Spawn bestaetigt ist.
  useEffect(() => {
    if (!pendingTerminalCommand) return;
    if (shells.length === 0) return;
    if (status !== "idle") return;
    if (!termRef.current) return;

    const wanted = pendingTerminalCommand.shell;
    const resolved =
      shells.find((s) => s.id === wanted)?.id
      ?? shells.find((s) => s.id.toLowerCase().includes("pwsh"))?.id
      ?? shells[0]?.id;
    if (!resolved) return;

    setSelectedShell(resolved);
    // Banner rendern, damit der User sieht, was gleich ausgefuehrt wird.
    const term = termRef.current;
    if (term) {
      term.clear();
      term.write(
        `\x1b[90m# ${pendingTerminalCommand.label}\x1b[0m\r\n`
        + `\x1b[90m# ${pendingTerminalCommand.command}\x1b[0m\r\n\r\n`,
      );
    }
    pendingInputRef.current = pendingTerminalCommand.command;
    setPendingTerminalCommand(null);
    start(resolved);
  }, [pendingTerminalCommand, shells, status, setPendingTerminalCommand]);

  const stop = () => {
    const id = terminalIdRef.current;
    if (!id) return;
    getWSClient()?.send({
      type: "command",
      action: "terminal.close",
      payload: { terminal_id: id },
    });
  };

  const isRunning = status === "running";
  const isStarting = status === "starting";

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<TerminalSquare className="h-4 w-4 text-primary" />}
        title={t("terminal_view.title")}
        subtitle={subtitleFor(status, exitCode, selectedShell, shells, t) || t("terminal_view.subtitle")}
        right={
          <div className="flex items-center gap-2">
            <select
              value={selectedShell}
              onChange={(e) => setSelectedShell(e.target.value)}
              disabled={isStarting || isRunning}
              className={cn(
                "h-8 rounded-md border border-border bg-background/40 px-2 text-xs",
                "focus:outline-none focus:ring-1 focus:ring-primary/40",
                "disabled:opacity-50",
              )}
            >
              {shells.length === 0 && (
                <option value="">Keine Shell gefunden</option>
              )}
              {shells.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            {isRunning ? (
              <Button size="sm" variant="ghost" onClick={stop}>
                <Square className="h-3.5 w-3.5" />
                <span className="ml-1 text-xs">Stop</span>
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => start()}
                disabled={isStarting || !selectedShell}
              >
                {isStarting ? (
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                <span className="ml-1 text-xs">
                  {status === "closed" ? t("terminal_view.restart") : t("terminal_view.start")}
                </span>
              </Button>
            )}
          </div>
        }
      />

      <div className="flex flex-1 min-h-0">
        <div className="relative flex-1 min-h-0 bg-[#0a0a0a]">
          <div ref={containerRef} className="absolute inset-0 px-3 py-2" />
          {status === "idle" && !cliConnectCoach && (
            <EmptyOverlay onStart={() => start()} canStart={!!selectedShell} />
          )}
        </div>
        {cliConnectCoach && <CliConnectCoach coach={cliConnectCoach} />}
      </div>
    </div>
  );
}

function subtitleFor(
  status: Status,
  exitCode: number | null,
  selectedShell: string,
  shells: ShellInfo[],
  t: (k: string) => string,
): string {
  const label =
    shells.find((s) => s.id === selectedShell)?.label ?? selectedShell ?? "—";
  switch (status) {
    case "idle":
      return t("terminal_view.status_idle").replace("{0}", label);
    case "starting":
      return t("terminal_view.status_starting").replace("{0}", label);
    case "running":
      return t("terminal_view.status_running").replace("{0}", label);
    case "closed":
      return t("terminal_view.status_closed").replace("{0}", label).replace("{1}", String(exitCode ?? "?"));
  }
}

function EmptyOverlay({
  onStart,
  canStart,
}: {
  onStart: () => void;
  canStart: boolean;
}) {
  const t = useT();
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
      <TerminalSquare className="h-10 w-10 text-muted-foreground/40" />
      <p className="text-sm text-muted-foreground">
        {t("terminal_view.select_shell_hint")}
      </p>
      <Button size="sm" onClick={onStart} disabled={!canStart}>
        <Play className="h-3.5 w-3.5" />
        <span className="ml-1.5">{t("terminal_view.start_shell")}</span>
      </Button>
    </div>
  );
}
