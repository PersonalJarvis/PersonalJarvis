import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import * as Dialog from "@radix-ui/react-dialog";
import { EyeOff, Eye, Pencil, Trash2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { findLead, useRetireAgent, type SocietyAgent } from "../data";

interface Props {
  agent: SocietyAgent;
  roster: SocietyAgent[];
  sample: boolean;
  hidden: boolean;
  x: number;
  y: number;
  onVisibilityChange: (agentId: string, hidden: boolean) => void;
  onDismiss: () => void;
}

export function AgentRosterActions({ agent, roster, sample, hidden, x, y, onVisibilityChange, onDismiss }: Props) {
  const t = useT();
  const client = useQueryClient();
  const retire = useRetireAgent();
  const menuRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"rename" | "delete" | null>(null);
  const [name, setName] = useState(agent.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const isLead = agent.tier === "lead";

  useEffect(() => {
    if (mode) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onDismiss();
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("scroll", onDismiss, true);
    window.addEventListener("resize", onDismiss);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("scroll", onDismiss, true);
      window.removeEventListener("resize", onDismiss);
    };
  }, [mode, onDismiss]);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu || mode) return;
    const { width, height } = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - height - 8))}px`;
    menu.style.visibility = "visible";
    menu.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus();
  }, [x, y, mode]);

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') ?? []);
    if (!items.length) return;
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  };

  const rename = useCallback(async () => {
    const cleaned = name.trim();
    if (!cleaned || cleaned === agent.name || busy) return;
    setBusy(true);
    setError(false);
    try {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: cleaned }),
      });
      if (!response.ok) throw new Error(`rename ${response.status}`);
      await client.invalidateQueries({ queryKey: ["society", "roster"] });
      onDismiss();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }, [agent.agentId, agent.name, busy, client, name, onDismiss]);

  const remove = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(false);
    try {
      await retire(agent, findLead(roster));
      onDismiss();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }, [agent, busy, onDismiss, retire, roster]);

  const closeDialog = (open: boolean) => {
    if (!open && !busy) onDismiss();
  };

  return createPortal(<>
    {!mode && <div ref={menuRef} role="menu" aria-label={t("society.roster.menu")} onKeyDown={onMenuKeyDown}
      style={{ width: 260, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-float">
      <button type="button" role="menuitem" disabled={sample || isLead} onClick={() => { setMode("rename"); setError(false); }}
        className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-secondary focus:bg-secondary focus:outline-none disabled:opacity-40">
        <Pencil className="h-4 w-4" aria-hidden />{t("society.roster.rename")}
      </button>
      <button type="button" role="menuitem" onClick={() => { onVisibilityChange(agent.agentId, !hidden); onDismiss(); }}
        className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-secondary focus:bg-secondary focus:outline-none">
        {hidden ? <Eye className="h-4 w-4" aria-hidden /> : <EyeOff className="h-4 w-4" aria-hidden />}
        {t(hidden ? "society.roster.show" : "society.roster.hide")}
      </button>
      <div className="my-1 border-t border-border" aria-hidden />
      <button type="button" role="menuitem" disabled={isLead} onClick={() => { setMode("delete"); setError(false); }}
        className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10 focus:bg-destructive/10 focus:outline-none disabled:opacity-40">
        <Trash2 className="h-4 w-4" aria-hidden />{t("society.roster.delete")}
      </button>
    </div>}
    <Dialog.Root open={mode !== null} onOpenChange={closeDialog}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[101] bg-scrim/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[102] w-[min(400px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-float outline-none">
          <Dialog.Title className="text-base font-semibold">{t(mode === "rename" ? "society.roster.rename" : "society.roster.delete")}</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-muted-foreground">
            {mode === "rename" ? t("society.roster.rename_hint") : t("society.roster.delete_confirm").replace("{0}", agent.name)}
          </Dialog.Description>
          {mode === "rename" && <form onSubmit={(event) => { event.preventDefault(); void rename(); }}>
            <input autoFocus value={name} maxLength={40} onChange={(event) => setName(event.target.value)}
              aria-label={t("society.roster.name")}
              className="mt-4 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            {error && <p role="alert" className="mt-2 text-sm text-destructive">{t("society.roster.rename_error")}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <Button type="button" variant="ghost" disabled={busy} onClick={onDismiss}>{t("society.roster.cancel")}</Button>
              <Button type="submit" disabled={busy || !name.trim() || name.trim() === agent.name}>{t("society.roster.save")}</Button>
            </div>
          </form>}
          {mode === "delete" && <>
            {error && <p role="alert" className="mt-2 text-sm text-destructive">{t("society.roster.delete_error")}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" disabled={busy} onClick={onDismiss}>{t("society.roster.cancel")}</Button>
              <Button variant="destructive" disabled={busy} onClick={() => void remove()}>{t("society.roster.delete")}</Button>
            </div>
          </>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </>, document.body);
}
