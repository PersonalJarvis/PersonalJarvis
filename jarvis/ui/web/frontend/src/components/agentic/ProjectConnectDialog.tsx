import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowLeft, Folder, FolderOpen, FolderPlus, Loader2, X } from "lucide-react";
import { fetchFolders } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { FolderPicker } from "./FolderPicker";

interface Props {
  onClose: () => void;
  onConnect: (path: string, name?: string) => Promise<void>;
}

const focusRing = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card";
const secondaryButton = `inline-flex h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50 ${focusRing}`;
const primaryButton = `inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40 ${focusRing}`;

/** A project summary with a separate, explicitly confirmed folder selection. */
export function ProjectConnectDialog({ onClose, onConnect }: Props) {
  const [name, setName] = useState("");
  const [path, setPath] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const nameInput = useRef<HTMLInputElement>(null);
  const pickerRegion = useRef<HTMLDivElement>(null);
  const folderName = path?.split(/[\\/]/).filter(Boolean).at(-1) || path;

  useEffect(() => {
    if (choosing) pickerRegion.current?.querySelector<HTMLInputElement>("input")?.focus();
    else nameInput.current?.focus();
  }, [choosing]);

  const choose = () => {
    setCandidate(path);
    setError(null);
    setChoosing(true);
  };
  const back = () => { setError(null); setChoosing(false); };

  const perform = async (operation: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not connect this folder. Try again.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const confirmFolder = () => {
    if (!candidate) return;
    void perform(async () => {
      // Recents and pasted paths may have disappeared since they were listed.
      const folder = await fetchFolders(candidate, true);
      if (folder.error) throw new Error(folder.error);
      if (!folder.path) throw new Error("Choose a folder to continue.");
      setPath(folder.path);
      setChoosing(false);
    });
  };

  return <Dialog.Root open onOpenChange={(open) => { if (!open && !inFlight.current) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[80] bg-background/70 backdrop-blur-sm" />
      <Dialog.Content
        aria-busy={busy}
        onOpenAutoFocus={(event) => { event.preventDefault(); nameInput.current?.focus(); }}
        onEscapeKeyDown={(event) => {
          if (inFlight.current || (event.target instanceof Element && event.target.closest("[data-escape-local]"))) event.preventDefault();
          else if (choosing) { event.preventDefault(); back(); }
        }}
        onInteractOutside={(event) => { if (inFlight.current) event.preventDefault(); }}
        className={cn("fixed left-1/2 top-1/2 z-[90] flex max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-card text-card-foreground shadow-2xl", choosing ? "h-[min(42rem,calc(100dvh-2rem))] max-w-3xl" : "max-w-xl")}
      >
        <header className="flex shrink-0 items-center gap-3 px-6 pb-5 pt-6 sm:px-7">
          {choosing && <button type="button" aria-label="Back to project" disabled={busy} onClick={back} className={cn("-ml-2 rounded-lg p-2 text-muted-foreground hover:bg-muted", focusRing)}><ArrowLeft className="h-5 w-5" /></button>}
          <Dialog.Title className="flex-1 text-xl font-semibold tracking-tight">{choosing ? "Choose a folder" : "Connect project"}</Dialog.Title>
          <Dialog.Close disabled={busy} aria-label="Close" className={cn("-mr-2 rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50", focusRing)}><X className="h-5 w-5" /></Dialog.Close>
          <Dialog.Description className="sr-only">Choose a source folder and an optional project name.</Dialog.Description>
        </header>

        {choosing ? <>
          <fieldset disabled={busy} className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 disabled:opacity-60">
            <div ref={pickerRegion} className="flex min-h-0 flex-1 flex-col">
              <FolderPicker selected={candidate} onSelect={(next) => { setCandidate(next); setError(null); }} />
            </div>
          </fieldset>
          <footer className="shrink-0 border-t border-border px-6 py-4 sm:px-7">
            {error && <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={candidate ?? undefined}>{candidate || "Choose a folder to continue"}</p>
              <div className="flex gap-2">
                <button type="button" disabled={busy} onClick={back} className={secondaryButton}>Back</button>
                <button type="button" disabled={busy || !candidate} onClick={confirmFolder} className={primaryButton}>{busy && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}Use this folder</button>
              </div>
            </div>
          </footer>
        </> : <form className="min-h-0 overflow-y-auto" onSubmit={(event) => { event.preventDefault(); if (path) void perform(() => onConnect(path, name.trim() || undefined)); }}>
          <fieldset disabled={busy} className="space-y-6 px-6 pb-7 sm:px-7">
            <label className="block">
              <span className="sr-only">Project name</span>
              <span className="flex h-12 items-center gap-3 rounded-xl border border-input bg-background/40 px-4 focus-within:border-ring focus-within:ring-1 focus-within:ring-ring">
                <Folder className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <input ref={nameInput} value={name} onChange={(event) => setName(event.target.value)} placeholder={folderName || "Project name (optional)"} className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground disabled:opacity-50" />
              </span>
            </label>
            <section aria-label="Source folder">
              <h3 className="mb-3 text-sm font-medium">Source folder</h3>
              {path ? <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/20 p-4">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-muted"><FolderOpen className="h-5 w-5 text-muted-foreground" aria-hidden="true" /></div>
                <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{folderName}</p><p title={path} className="mt-1 break-all text-xs leading-relaxed text-muted-foreground">{path}</p></div>
                <button type="button" onClick={choose} className={cn(secondaryButton, "shrink-0 px-3")}>Change</button>
              </div> : <div className="flex min-h-36 flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-border bg-muted/10 p-5">
                <p className="text-center text-sm text-muted-foreground">Add a folder from this computer</p>
                <button type="button" onClick={choose} className={cn(secondaryButton, "border border-border bg-muted/60 text-foreground")}><FolderPlus className="h-4 w-4" aria-hidden="true" />Choose folder</button>
              </div>}
            </section>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          </fieldset>
          <footer className="flex justify-end gap-2 px-6 pb-6 sm:px-7">
            <button type="button" disabled={busy} onClick={onClose} className={secondaryButton}>Cancel</button>
            <button type="submit" disabled={busy || !path} className={primaryButton}>{busy && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}{busy ? "Connecting…" : "Connect project"}</button>
          </footer>
        </form>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
