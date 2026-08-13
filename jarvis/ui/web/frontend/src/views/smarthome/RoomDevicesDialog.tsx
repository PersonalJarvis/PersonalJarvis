/**
 * Put devices into a room.
 *
 * Deliberately shows THREE groups, not just the free ones:
 *
 * 1. devices already in this room, so removing is possible from the same place
 *    adding is — the alternative sends people hunting for a second screen;
 * 2. devices in no room at all, which is what a fresh house looks like;
 * 3. devices currently in another room, labelled with where they are now.
 *
 * Group 3 is the important one. A hub's areas are frequently wrong for how
 * someone actually lives (the hall lamp that is really the kitchen's), and a
 * picker that hid those devices would make the correction impossible without
 * first editing the hub — which is precisely the duplicated setup this section
 * exists to remove. Moving a device is one tap and the server takes it out of
 * its old room, so it can never end up in two.
 */
import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, Loader2, Search, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { SmartDevice, SmartRoom } from "@/hooks/useSmartHome";
import { iconForKind } from "@/views/smarthome/deviceMeta";

export interface RoomDevicesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  room: SmartRoom;
  devices: SmartDevice[];
  rooms: SmartRoom[];
  unassigned: string[];
  onAssign: (roomId: string, deviceIds: string[]) => Promise<string | null>;
  onUnassign: (roomId: string, deviceId: string) => Promise<string | null>;
}

export function RoomDevicesDialog({
  open,
  onOpenChange,
  room,
  devices,
  rooms,
  unassigned,
  onAssign,
  onUnassign,
}: RoomDevicesDialogProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");

  /** Which room each device currently sits in — so group 3 can say where. */
  const homeOf = useMemo(() => {
    const map = new Map<string, SmartRoom>();
    for (const candidate of rooms) {
      for (const deviceId of candidate.device_ids) map.set(deviceId, candidate);
    }
    return map;
  }, [rooms]);

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = (device: SmartDevice) =>
      !needle ||
      device.name.toLowerCase().includes(needle) ||
      (device.room ?? "").toLowerCase().includes(needle);

    const mine: SmartDevice[] = [];
    const free: SmartDevice[] = [];
    const elsewhere: SmartDevice[] = [];
    for (const device of devices) {
      if (!matches(device)) continue;
      if (room.device_ids.includes(device.id)) mine.push(device);
      else if (unassigned.includes(device.id)) free.push(device);
      else elsewhere.push(device);
    }
    return { mine, free, elsewhere };
  }, [devices, query, room.device_ids, unassigned]);

  const move = async (device: SmartDevice, into: boolean) => {
    setBusyId(device.id);
    setError("");
    const failure = into
      ? await onAssign(room.id, [device.id])
      : await onUnassign(room.id, device.id);
    setBusyId(null);
    if (failure) setError(failure);
  };

  const total = groups.mine.length + groups.free.length + groups.elsewhere.length;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="room-devices-dialog"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[min(88dvh,46rem)] w-[min(560px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card shadow-[0_28px_90px_-24px_rgba(0,0,0,0.75)] outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                {t("smarthome.rooms.devices_title").replace("{room}", room.name)}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {t("smarthome.rooms.devices_hint")}
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
              aria-label={t("common.close")}
            >
              <X className="h-4 w-4" aria-hidden />
            </Dialog.Close>
          </header>

          <div className="border-b border-border px-5 py-3">
            <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2">
              <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("smarthome.search_placeholder")}
                className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground/70"
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            {total === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                {query ? t("smarthome.no_matches") : t("smarthome.rooms.no_devices_at_all")}
              </p>
            ) : (
              <div className="space-y-5">
                <Group
                  title={t("smarthome.rooms.group_in_room")}
                  devices={groups.mine}
                  render={(device) => (
                    <DeviceRow
                      key={device.id}
                      device={device}
                      selected
                      busy={busyId === device.id}
                      onToggle={() => void move(device, false)}
                    />
                  )}
                />
                <Group
                  title={t("smarthome.rooms.group_free")}
                  devices={groups.free}
                  render={(device) => (
                    <DeviceRow
                      key={device.id}
                      device={device}
                      busy={busyId === device.id}
                      onToggle={() => void move(device, true)}
                    />
                  )}
                />
                <Group
                  title={t("smarthome.rooms.group_elsewhere")}
                  devices={groups.elsewhere}
                  render={(device) => (
                    <DeviceRow
                      key={device.id}
                      device={device}
                      note={homeOf.get(device.id)?.name}
                      busy={busyId === device.id}
                      onToggle={() => void move(device, true)}
                    />
                  )}
                />
              </div>
            )}
            {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
          </div>

          <footer className="flex justify-end border-t border-border px-5 py-3">
            <Dialog.Close className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
              {t("common.done")}
            </Dialog.Close>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Group({
  title,
  devices,
  render,
}: {
  title: string;
  devices: SmartDevice[];
  render: (device: SmartDevice) => React.ReactNode;
}) {
  if (devices.length === 0) return null;
  return (
    <section>
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
        <span className="ml-1.5 font-normal tabular-nums opacity-70">{devices.length}</span>
      </h4>
      <div className="space-y-1">{devices.map(render)}</div>
    </section>
  );
}

function DeviceRow({
  device,
  selected = false,
  note,
  busy,
  onToggle,
}: {
  device: SmartDevice;
  selected?: boolean;
  /** Where the device currently lives, when that is somewhere else. */
  note?: string;
  busy: boolean;
  onToggle: () => void;
}) {
  const Icon = iconForKind(device.kind);
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={busy}
      data-testid={`room-device-row-${device.id}`}
      aria-pressed={selected}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
        selected
          ? "border-primary/40 bg-primary/[0.07]"
          : "border-border bg-secondary/30 hover:border-primary/30",
        busy && "opacity-60",
      )}
    >
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-foreground">{device.name}</span>
        {note && (
          <span className="block truncate text-xs text-muted-foreground">{note}</span>
        )}
      </span>
      {busy ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
      ) : (
        <span
          className={cn(
            "flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors",
            selected ? "border-primary bg-primary text-primary-foreground" : "border-border",
          )}
        >
          {selected && <Check className="h-3 w-3" aria-hidden />}
        </span>
      )}
    </button>
  );
}
