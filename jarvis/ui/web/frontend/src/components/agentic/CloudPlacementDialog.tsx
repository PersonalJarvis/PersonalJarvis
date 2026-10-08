import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, Cloud, CloudUpload, Laptop, Loader2, RefreshCw, Server } from "lucide-react";
import { computersApi, readinessApi, type Computer, type Readiness } from "@/lib/computersApi";
import type { TerminalState } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

interface Props {
  terminal: TerminalState;
  initialTarget?: string | null;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: (computerId: string | null, targetName: string) => void;
}

const messageOf = (error: unknown) => error instanceof Error ? error.message : String(error);
const isWindows = (computer: Computer, readiness?: Readiness | null) =>
  /windows/i.test(`${computer.facts?.os_id ?? ""} ${computer.facts?.os_name ?? ""} ${readiness?.os ?? ""}`);

/** A target's connection is checked on selection; no agent or paid provider is started. */
export function CloudPlacementDialog({ terminal, initialTarget, busy, error, onCancel, onConfirm }: Props) {
  const [computers, setComputers] = useState<Computer[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<string | null | undefined>(initialTarget === undefined && terminal.computer_id ? null : initialTarget);
  const [check, setCheck] = useState<{ id: string; computer?: Computer; readiness?: Readiness; error?: string } | null>(null);
  const [checkVersion, setCheckVersion] = useState(0);
  const submitted = useRef(false);
  const setActiveSection = useEventStore((state) => state.setActiveSection);
  const currentId = terminal.computer_id || null;
  const chosen = computers.find((computer) => computer.id === selected);
  const activeCheck = selected && check?.id === selected ? check : null;
  const target = activeCheck?.computer ?? chosen;
  const readiness = activeCheck?.readiness;
  const checkError = activeCheck?.error;
  const checking = Boolean(selected && chosen && check?.id !== selected);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setLoadError("");
    void computersApi.list().then((list) => {
      if (live) setComputers(list);
    }).catch((failure: unknown) => {
      if (live) setLoadError(messageOf(failure));
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [reload]);

  useEffect(() => {
    if (!selected || !chosen || currentId || chosen.busy || chosen.health.status === "provisioning") return;
    let live = true;
    setCheck(null);
    void computersApi.check(selected).then(async (computer) => {
      if (!live) return;
      if (computer.health.status !== "online") {
        setCheck({ id: selected, computer, error: computer.health.message || "This server is not reachable. Check its connection in Computers." });
        return;
      }
      const next = await readinessApi.get(selected);
      if (live) setCheck({ id: selected, computer, readiness: next });
    }).catch((failure: unknown) => {
      if (live) setCheck({ id: selected, error: messageOf(failure) });
    });
    return () => { live = false; };
  }, [selected, chosen, currentId, checkVersion]);

  useEffect(() => { if (!busy) submitted.current = false; }, [busy, error]);
  const windows = target ? isWindows(target, readiness) : false;
  const requiredTools = readiness ? ["git", ...(!windows ? ["tmux"] : []), ...(["claude", "codex"].includes(terminal.agent) ? [terminal.agent] : [])] : [];
  const missingTools = requiredTools.filter((id) => !readiness?.tools.some((tool) => tool.id === id && tool.installed && !tool.outdated));
  const missingLogin = readiness && (terminal.agent === "claude" || terminal.agent === "codex") && !readiness.logins[terminal.agent];
  const unchanged = selected === currentId;
  const allowed = !busy && !loading && !loadError && !unchanged && (selected === null || Boolean(!currentId && target && readiness && !checkError && !missingTools.length && !missingLogin));
  const targetName = selected === null ? "this computer" : target?.name ?? "a server";
  const currentName = computers.find((computer) => computer.id === currentId)?.name ?? (currentId ? "another computer" : "This computer");
  const manageComputers = () => { if (!busy) { onCancel(); setActiveSection("computers"); } };
  const pick = (id: string | null) => { if (!busy) { submitted.current = false; setSelected(id); } };
  const submit = () => {
    if (!allowed || selected === undefined || submitted.current) return;
    submitted.current = true;
    onConfirm(selected, targetName);
  };

  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onCancel(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm" />
      <Dialog.Content aria-busy={busy} className="fixed left-1/2 top-1/2 z-[90] max-h-[calc(100dvh-2rem)] w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-2xl">
        <form onSubmit={(event) => { event.preventDefault(); submit(); }}>
          <div className="flex items-start gap-3">
            <Cloud className="mt-0.5 h-6 w-6 shrink-0 text-accent" aria-hidden="true" />
            <div className="min-w-0">
              <Dialog.Title className="text-base font-semibold">{currentId ? `Cloud session: ${terminal.name}` : `Move ${terminal.name} to a server`}</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">Choose where this coding session runs. Its pane stays available here after the move.</Dialog.Description>
            </div>
          </div>
          <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground"><span>Currently on: {currentName}</span>{target && !unchanged && <><ArrowRight className="h-3.5 w-3.5" aria-hidden="true" /><span className="truncate">{target.name}</span></>}</p>

          <fieldset disabled={busy || loading} className="mt-4">
            <legend className="text-sm font-medium">Destination</legend>
            {loading && <p role="status" className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />Loading connected computers…</p>}
            {!loading && !loadError && <div className="mt-2 grid max-h-56 gap-2 overflow-y-auto" role="radiogroup" aria-label="Destination">
              {currentId && <label className={cn("flex cursor-pointer items-center gap-3 rounded-lg border p-3", selected === null ? "border-accent bg-accent-soft" : "border-border")}>
                <input type="radio" name="cloud-destination" value="local" checked={selected === null} onChange={() => pick(null)} className="accent-accent" />
                <Laptop className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span><span className="block text-sm font-medium">Bring back to this computer</span><span className="block text-xs text-muted-foreground">Stops when this PC sleeps or shuts down</span></span>
              </label>}
              {computers.map((computer) => <label key={computer.id} className={cn("flex items-center gap-3 rounded-lg border p-3", computer.id === selected ? "border-accent bg-accent-soft" : "border-border", computer.busy || currentId ? "opacity-50" : "cursor-pointer")}>
                <input type="radio" name="cloud-destination" value={computer.id} checked={selected === computer.id}
                  disabled={computer.busy || computer.health.status === "provisioning" || Boolean(currentId)}
                  onChange={() => pick(computer.id)} className="accent-accent" />
                <Server className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="min-w-0"><span className="block truncate text-sm font-medium">{computer.name}{computer.id === currentId ? " · Current" : ""}</span>
                  <span className="block text-xs text-muted-foreground">{computer.kind === "local_vm" ? "Local VM" : computer.provider_name || "Server"}{computer.region ? ` · ${computer.region}` : ""} · {computer.health.status === "unknown" ? "Not checked yet" : computer.health.status.replaceAll("_", " ")}</span>
                </span>
              </label>)}
            </div>}
          </fieldset>
          {currentId && <p className="mt-2 text-xs text-muted-foreground">Bring this session back before moving it to a different server, so its latest changes travel with it.</p>}
          {loadError && <div className="mt-3"><p role="alert" className="text-sm text-destructive">Could not load connected computers: {loadError}</p><button type="button" onClick={() => setReload((value) => value + 1)} className="mt-2 text-sm font-medium text-accent">Try again</button></div>}
          {!loading && !loadError && computers.length === 0 && <p className="mt-3 text-sm text-muted-foreground">No servers connected yet. Connect a VPS to move work off this PC.</p>}
          <button type="button" disabled={busy} onClick={manageComputers} className="mt-3 text-xs font-medium text-accent underline-offset-4 hover:underline disabled:opacity-50">{computers.length ? "Manage connected computers" : "Connect a server"}</button>

          {selected !== undefined && !unchanged && <div className="mt-5 rounded-xl border border-border bg-muted/30 p-4 text-xs leading-relaxed text-muted-foreground">
            <p className="text-sm font-medium text-foreground">What moves with {terminal.name}</p>
            <p className="mt-2">The project snapshot includes uncommitted changes. Secret files are excluded. The source process stops during the move; wait for confirmation before closing this PC.</p>
            <p className="mt-2">{terminal.agent === "claude"
              ? "Claude conversation history moves when available. An interrupted turn can continue through native session resume; check the destination pane for its actual state."
              : terminal.agent === "codex" ? "Codex conversation history moves when available. Send the next prompt on the destination to continue; an interrupted turn is not replayed."
                : "This CLI starts a new session on the destination. Conversation history and in-progress work cannot be resumed automatically."}</p>
            {selected === null ? <p className="mt-2">Remote changes return to the local project. Local file conflicts are checked before replacement.</p>
              : target?.kind === "local_vm" ? <p className="mt-2">This VM runs on this PC. It stops when this PC sleeps or shuts down.</p>
                : windows ? <p className="mt-2">Windows sessions need this PC to stay connected. Choose a Linux VPS to keep working with this PC off.</p>
                  : readiness ? <p className="mt-2">On this server, the coding process can stay running after this PC disconnects. Return to this pane to reconnect, or bring the session back.</p> : null}
          </div>}
          {checking && <p role="status" className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />Checking connection, tools and login…</p>}
          {checkError && <p role="alert" className="mt-3 text-xs text-destructive">{checkError}</p>}
          {readiness && missingTools.length > 0 && <p role="alert" className="mt-3 text-xs text-destructive">Install or update on {targetName}: {missingTools.join(", ")}. Open Computers to set up this server.</p>}
          {missingLogin && <p role="alert" className="mt-3 text-xs text-destructive">Sign in to {terminal.display_name} on {targetName} before moving this session. Open Computers to set up its login.</p>}
          {selected && (checkError || readiness) && <button type="button" disabled={busy} onClick={() => { setCheck(null); setCheckVersion((value) => value + 1); }} className="mt-2 inline-flex items-center gap-1.5 text-xs text-accent disabled:opacity-50"><RefreshCw className="h-3 w-3" aria-hidden="true" />Check again</button>}
          {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
          {busy && <p role="status" className="mt-3 text-sm text-muted-foreground">Moving files and preparing the destination. Keep this PC connected…</p>}
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" disabled={busy} onClick={onCancel} className="rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">Cancel</button>
            <button type="submit" disabled={!allowed} className="flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <CloudUpload className="h-4 w-4" aria-hidden="true" />}
              {selected === null ? "Bring session back" : target ? `Move to ${target.name}` : "Move session"}
            </button>
          </div>
        </form>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
