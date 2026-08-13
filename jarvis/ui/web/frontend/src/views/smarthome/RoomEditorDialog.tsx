/**
 * Create a room, or change one that exists.
 *
 * The screen that was missing entirely: until this dialog, a room was whatever
 * a hub reported and there was no way to make, name, or arrange one.
 *
 * Four fields, because that is everything a room IS on this side — a name, a
 * glyph, a colour, and optionally which storey it sits on. Devices are assigned
 * from the room itself rather than here: picking a lamp is a different kind of
 * decision from naming a place, and merging the two would produce the sprawling
 * settings form this section is trying not to be.
 *
 * The icon and colour choices come from the SERVER (`layout.icons`,
 * `layout.colors`). A hardcoded copy here is a copy that drifts the moment the
 * backend grows a new one, and the drift is silent.
 */
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, Loader2, Trash2, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { RoomDraft, SmartRoom } from "@/hooks/useSmartHome";
import { iconForRoom, paletteFor } from "@/views/smarthome/roomMeta";

export interface RoomEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The room being edited, or null to create a new one. */
  editing: SmartRoom | null;
  icons: string[];
  colors: string[];
  onSave: (draft: RoomDraft) => Promise<string | null>;
  onDelete?: (roomId: string) => Promise<string | null>;
}

export function RoomEditorDialog({
  open,
  onOpenChange,
  editing,
  icons,
  colors,
  onSave,
  onDelete,
}: RoomEditorDialogProps) {
  const t = useT();
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("room");
  const [color, setColor] = useState("slate");
  const [floor, setFloor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  /*
   * Refill every time the dialog OPENS, not just when the room changes.
   *
   * The same instance serves "edit kitchen", then "add new", then "edit
   * kitchen again"; without `open` in the dependencies the third visit would
   * still show whatever was half-typed during the second.
   */
  useEffect(() => {
    if (!open) return;
    setName(editing?.name ?? "");
    setIcon(editing?.icon ?? "room");
    setColor(editing?.color ?? colors[0] ?? "slate");
    setFloor(editing?.floor ?? "");
    setError("");
    setConfirmDelete(false);
  }, [open, editing, colors]);

  const trimmed = name.trim();
  const canSave = Boolean(trimmed) && !busy;

  const save = async () => {
    if (!canSave) return;
    setBusy(true);
    setError("");
    // `floor` is sent as an empty string rather than omitted when cleared: the
    // backend reads "" as "remove the storey" and a missing key as "leave it".
    const failure = await onSave({ name: trimmed, icon, color, floor: floor.trim() });
    setBusy(false);
    if (failure) setError(failure);
    else onOpenChange(false);
  };

  const remove = async () => {
    if (!editing || !onDelete) return;
    setBusy(true);
    const failure = await onDelete(editing.id);
    setBusy(false);
    if (failure) setError(failure);
    else onOpenChange(false);
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="room-editor-dialog"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[min(88dvh,42rem)] w-[min(520px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card shadow-[0_28px_90px_-24px_rgba(0,0,0,0.75)] outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
          onKeyDown={(event) => {
            // Enter saves from anywhere in the form. A dialog with one obvious
            // action that still demands a mouse is one people stop opening.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void save();
            }
          }}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                {editing ? t("smarthome.rooms.edit_title") : t("smarthome.rooms.new_title")}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {t("smarthome.rooms.editor_hint")}
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
              aria-label={t("common.close")}
            >
              <X className="h-4 w-4" aria-hidden />
            </Dialog.Close>
          </header>

          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
            <Field label={t("smarthome.rooms.field_name")}>
              <input
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("smarthome.rooms.name_placeholder")}
                maxLength={60}
                data-testid="room-name-input"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground/70 focus:border-primary/50"
              />
            </Field>

            <Field label={t("smarthome.rooms.field_icon")}>
              <div className="grid grid-cols-8 gap-1.5">
                {icons.map((option) => {
                  const Icon = iconForRoom(option);
                  const active = option === icon;
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => setIcon(option)}
                      aria-label={option}
                      aria-pressed={active}
                      className={cn(
                        "flex aspect-square items-center justify-center rounded-lg border transition-colors",
                        active
                          ? "border-primary/50 bg-primary/10 text-primary"
                          : "border-border bg-secondary/40 text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <Icon className="h-4 w-4" aria-hidden />
                    </button>
                  );
                })}
              </div>
            </Field>

            <Field label={t("smarthome.rooms.field_color")}>
              <div className="flex flex-wrap gap-2">
                {colors.map((option) => {
                  const palette = paletteFor(option);
                  const active = option === color;
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => setColor(option)}
                      aria-label={option}
                      aria-pressed={active}
                      className={cn(
                        "flex h-8 w-8 items-center justify-center rounded-full transition-transform",
                        palette.swatch,
                        active
                          ? "ring-2 ring-foreground/70 ring-offset-2 ring-offset-card"
                          : "hover:scale-105",
                      )}
                    >
                      {active && <Check className="h-4 w-4 text-white" aria-hidden />}
                    </button>
                  );
                })}
              </div>
            </Field>

            <Field label={t("smarthome.rooms.field_floor")} optional>
              <input
                value={floor}
                onChange={(event) => setFloor(event.target.value)}
                placeholder={t("smarthome.rooms.floor_placeholder")}
                maxLength={40}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground/70 focus:border-primary/50"
              />
            </Field>

            {error && (
              <p data-testid="room-editor-error" className="text-xs text-destructive">
                {error}
              </p>
            )}
          </div>

          <footer className="flex items-center gap-2 border-t border-border px-5 py-3">
            {editing && onDelete && (
              /* Two clicks, in place. A confirmation dialog stacked on a dialog
                 is a stack people dismiss without reading, and deleting a room
                 is recoverable anyway — the devices come straight back as
                 unassigned rather than disappearing with it. */
              <button
                type="button"
                onClick={() => (confirmDelete ? void remove() : setConfirmDelete(true))}
                onBlur={() => setConfirmDelete(false)}
                data-testid="room-delete"
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                  confirmDelete
                    ? "border-destructive/50 bg-destructive/10 text-destructive"
                    : "border-border text-muted-foreground hover:border-destructive/40 hover:text-destructive",
                )}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
                {confirmDelete
                  ? t("smarthome.rooms.delete_confirm")
                  : t("smarthome.rooms.delete")}
              </button>
            )}
            <div className="flex-1" />
            <Dialog.Close className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
              {t("common.cancel")}
            </Dialog.Close>
            <button
              type="button"
              disabled={!canSave}
              onClick={() => void save()}
              data-testid="room-save"
              className="flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
            >
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {t("common.save")}
            </button>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Field({
  label,
  optional = false,
  children,
}: {
  label: string;
  optional?: boolean;
  children: React.ReactNode;
}) {
  const t = useT();
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {label}
        {optional && (
          <span className="text-[10px] font-normal text-muted-foreground/70">
            {t("common.optional")}
          </span>
        )}
      </span>
      {children}
    </label>
  );
}
