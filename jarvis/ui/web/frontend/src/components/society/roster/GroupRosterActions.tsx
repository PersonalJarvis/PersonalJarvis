import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { deleteSocietyChatGroup, type SocietyChatGroup } from "@/lib/societyChatGroups";

interface Props {
  group: SocietyChatGroup;
  x: number;
  y: number;
  onEdit: () => void;
  onDismiss: () => void;
}

export function GroupRosterActions({ group, x, y, onEdit, onDismiss }: Props) {
  const t = useT();
  const client = useQueryClient();
  const menuRef = useRef<HTMLDivElement>(null);
  const [confirmUngroup, setConfirmUngroup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (confirmUngroup) return;
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
  }, [confirmUngroup, onDismiss]);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu || confirmUngroup) return;
    const { width, height } = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - height - 8))}px`;
    menu.style.visibility = "visible";
    menu.querySelector<HTMLButtonElement>("button")?.focus();
  }, [confirmUngroup, x, y]);

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  };

  const remove = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await deleteSocietyChatGroup(group.group_id);
      await client.invalidateQueries({ queryKey: ["society", "chat-groups"] });
      onDismiss();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return createPortal(<>
    {!confirmUngroup && <div ref={menuRef} role="menu" aria-label={group.name} onKeyDown={onMenuKeyDown}
      style={{ width: 240, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-float">
      <button type="button" role="menuitem" onClick={() => { onEdit(); onDismiss(); }}
        className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-secondary focus:bg-secondary focus:outline-none">
        <Pencil className="h-4 w-4" aria-hidden />{t("society.groups.edit")}
      </button>
      <button type="button" role="menuitem" onClick={() => setConfirmUngroup(true)}
        className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10 focus:bg-destructive/10 focus:outline-none">
        <Trash2 className="h-4 w-4" aria-hidden />{t("society.groups.delete")}
      </button>
    </div>}
    <Dialog.Root open={confirmUngroup} onOpenChange={(open) => { if (!open && !busy) onDismiss(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[101] bg-scrim/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[102] w-[min(400px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-float outline-none">
          <Dialog.Title className="text-base font-semibold">{t("society.groups.delete")}</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-muted-foreground">{t("society.groups.delete_confirm")}</Dialog.Description>
          {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" disabled={busy} onClick={onDismiss}>{t("society.groups.cancel")}</Button>
            <Button variant="destructive" disabled={busy} onClick={() => void remove()}>{t("society.groups.delete")}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </>, document.body);
}
