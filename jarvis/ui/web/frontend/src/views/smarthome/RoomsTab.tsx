/**
 * The house by room — an overview you can arrange, and a room you can open.
 *
 * Two screens in one tab, because that is how every smart-home app people
 * already know is shaped: a wall of rooms to glance at, and one room at a time
 * to actually operate. Flattening both into a single long list (what this tab
 * used to be) means the twelfth device of the first room sits between you and
 * the second room's light switch.
 *
 * What a room card carries is chosen from what people look for from across the
 * kitchen: how warm it is, how many things are on, and one tap to turn them
 * off. Not a device inventory — that is what opening it is for.
 *
 * Rooms belong to the USER here (see `jarvis/smarthome/home_layout.py`). They
 * can be created before any hardware exists, renamed without touching a hub,
 * and given an icon and a colour a hub has no field for. The platforms' own
 * areas arrive as suggestions, never as silent overwrites.
 */
import { useMemo, useState } from "react";
import {
  ArrowLeft,
  DoorClosed,
  Loader2,
  Pencil,
  Plus,
  Power,
  Sparkles,
  Star,
  Thermometer,
} from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type {
  CommandOutcome,
  RoomDraft,
  RoomLayoutPayload,
  SmartDevice,
  SmartRoom,
} from "@/hooks/useSmartHome";
import { DeviceCard } from "@/views/smarthome/DeviceCard";
import { iconForKind, isActive, renderStateLine, stateLine } from "@/views/smarthome/deviceMeta";
import { RoomDevicesDialog } from "@/views/smarthome/RoomDevicesDialog";
import { RoomEditorDialog } from "@/views/smarthome/RoomEditorDialog";
import {
  groupByKind,
  iconForRoom,
  paletteFor,
  roomActivity,
  tileFill,
} from "@/views/smarthome/roomMeta";

export interface RoomActions {
  create: (body: RoomDraft) => Promise<string | null>;
  update: (roomId: string, body: Partial<RoomDraft>) => Promise<string | null>;
  remove: (roomId: string) => Promise<string | null>;
  reorder: (roomIds: string[]) => Promise<string | null>;
  importAreas: () => Promise<string | null>;
  assign: (roomId: string, deviceIds: string[]) => Promise<string | null>;
  unassign: (roomId: string, deviceId: string) => Promise<string | null>;
  toggleFavorite: (roomId: string, deviceId: string) => Promise<string | null>;
}

export interface RoomsTabProps {
  devices: SmartDevice[];
  layout: RoomLayoutPayload;
  loading: boolean;
  actions: RoomActions;
  onCommand: (
    deviceId: string,
    command: string,
    args?: Record<string, unknown>,
    optimistic?: Record<string, unknown>,
    confirm?: boolean,
  ) => Promise<CommandOutcome>;
  onRoomCommand: (roomId: string, command: string) => Promise<boolean>;
}

export function RoomsTab({
  devices,
  layout,
  loading,
  actions,
  onCommand,
  onRoomCommand,
}: RoomsTabProps) {
  const t = useT();
  const [openRoomId, setOpenRoomId] = useState<string | null>(null);
  const [editing, setEditing] = useState<SmartRoom | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [devicePickerOpen, setDevicePickerOpen] = useState(false);
  const [busyRoom, setBusyRoom] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);

  const byId = useMemo(() => new Map(devices.map((d) => [d.id, d])), [devices]);
  /** Resolve a room's ids against the live device list, dropping stale ones. */
  const devicesOf = useMemo(
    () => (room: SmartRoom) =>
      room.device_ids.map((id) => byId.get(id)).filter((d): d is SmartDevice => Boolean(d)),
    [byId],
  );

  // Held by id, not by object: the room is replaced on every poll, and keeping
  // the object would leave the open room frozen at the state it had on open.
  const openRoom = layout.rooms.find((room) => room.id === openRoomId) ?? null;

  if (loading && layout.rooms.length === 0 && devices.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  const allOff = async (room: SmartRoom) => {
    setBusyRoom(room.id);
    await onRoomCommand(room.id, "turn_off");
    setBusyRoom(null);
  };

  const openEditor = (room: SmartRoom | null) => {
    setEditing(room);
    setEditorOpen(true);
  };

  const saveRoom = async (draft: RoomDraft) =>
    editing ? actions.update(editing.id, draft) : actions.create(draft);

  const deleteRoom = async (roomId: string) => {
    const failure = await actions.remove(roomId);
    if (!failure && openRoomId === roomId) setOpenRoomId(null);
    return failure;
  };

  return (
    <>
      <ScrollArea className="h-full">
        {openRoom ? (
          <RoomDetail
            room={openRoom}
            devices={devicesOf(openRoom)}
            busy={busyRoom === openRoom.id}
            onBack={() => setOpenRoomId(null)}
            onEdit={() => openEditor(openRoom)}
            onPickDevices={() => setDevicePickerOpen(true)}
            onAllOff={() => void allOff(openRoom)}
            onCommand={onCommand}
            onToggleFavorite={(deviceId) =>
              void actions.toggleFavorite(openRoom.id, deviceId)
            }
          />
        ) : (
          <div className="space-y-6 p-6">
            {layout.suggestions.length > 0 && (
              <SuggestionBanner
                count={layout.suggestions.length}
                busy={importing}
                onImport={async () => {
                  setImporting(true);
                  await actions.importAreas();
                  setImporting(false);
                }}
              />
            )}

            {layout.rooms.length === 0 ? (
              <EmptyRooms onCreate={() => openEditor(null)} />
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {layout.rooms.map((room) => (
                  <RoomCard
                    key={room.id}
                    room={room}
                    devices={devicesOf(room)}
                    busy={busyRoom === room.id}
                    onOpen={() => setOpenRoomId(room.id)}
                    onEdit={() => openEditor(room)}
                    onAllOff={() => void allOff(room)}
                  />
                ))}
                <button
                  type="button"
                  onClick={() => openEditor(null)}
                  data-testid="smarthome-add-room"
                  className="flex min-h-[8.5rem] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
                >
                  <Plus className="h-5 w-5" aria-hidden />
                  <span className="text-sm font-medium">{t("smarthome.rooms.add")}</span>
                </button>
              </div>
            )}

            {layout.unassigned.length > 0 && (
              <UnassignedNote count={layout.unassigned.length} />
            )}
          </div>
        )}
      </ScrollArea>

      <RoomEditorDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        editing={editing}
        icons={layout.icons}
        colors={layout.colors}
        onSave={saveRoom}
        onDelete={editing ? deleteRoom : undefined}
      />
      {openRoom && (
        <RoomDevicesDialog
          open={devicePickerOpen}
          onOpenChange={setDevicePickerOpen}
          room={openRoom}
          devices={devices}
          rooms={layout.rooms}
          unassigned={layout.unassigned}
          onAssign={actions.assign}
          onUnassign={actions.unassign}
        />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ overview */

/**
 * One room, read from across the room.
 *
 * The colour wash at the top gains strength with how much of the room is ON, so
 * a glance across the grid says which rooms are awake before a single number is
 * read. That is the difference this card is making: the state is drawn, not
 * spelled out.
 */
function RoomCard({
  room,
  devices,
  busy,
  onOpen,
  onEdit,
  onAllOff,
}: {
  room: SmartRoom;
  devices: SmartDevice[];
  busy: boolean;
  onOpen: () => void;
  onEdit: () => void;
  onAllOff: () => void;
}) {
  const t = useT();
  const palette = paletteFor(room.color);
  const Icon = iconForRoom(room.icon);
  const activity = roomActivity(devices);
  const activeCount = devices.filter(isActive).length;
  const canSwitchOff = devices.some(
    (d) => d.state.on_off === true && d.commands.includes("turn_off"),
  );

  return (
    <div
      data-testid={`smarthome-room-${room.id}`}
      data-active={activeCount > 0 ? "true" : "false"}
      className={cn(
        "group relative flex min-h-[8.5rem] flex-col overflow-hidden rounded-xl border transition-colors",
        palette.surface,
        activeCount > 0 ? palette.border : "border-border",
      )}
    >
      {/* The wash. Opacity tracks activity, so an untouched room stays calm and
          a busy one reads as busy without any text changing. */}
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b to-transparent transition-opacity duration-500",
          palette.wash,
        )}
        style={{ opacity: 0.35 + activity * 0.65 }}
      />

      <button
        type="button"
        onClick={onOpen}
        className="relative flex flex-1 flex-col items-start gap-2 p-4 text-left outline-none"
      >
        <div className={cn("flex items-center gap-2", palette.accent)}>
          <Icon className="h-5 w-5" aria-hidden />
          {room.floor && (
            <span className="text-[10px] font-medium uppercase tracking-wide opacity-70">
              {room.floor}
            </span>
          )}
        </div>
        <div className="min-w-0">
          <h3 className="truncate font-display text-base font-semibold text-foreground">
            {room.name}
          </h3>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {[
              room.temperature !== null
                ? `${room.temperature.toFixed(1).replace(/\.0$/, "")}°`
                : null,
              room.humidity !== null ? `${Math.round(room.humidity)}%` : null,
              activeCount > 0
                ? t("smarthome.rooms.active_count").replace("{count}", String(activeCount))
                : devices.length === 0
                  ? t("smarthome.rooms.no_devices_yet")
                  : t("smarthome.rooms.all_quiet"),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
      </button>

      <div className="relative flex items-center gap-1 px-4 pb-3">
        <span className="text-[11px] tabular-nums text-muted-foreground/80">
          {t("smarthome.rooms.device_total").replace("{count}", String(devices.length))}
        </span>
        <div className="flex-1" />
        {/* Only shown when it would DO something. A permanently visible button
            that is usually a no-op teaches people to stop trusting buttons. */}
        {canSwitchOff && (
          <IconButton
            label={t("smarthome.rooms.all_off")}
            busy={busy}
            onClick={onAllOff}
            testId={`smarthome-room-off-${room.id}`}
          >
            <Power className="h-3.5 w-3.5" aria-hidden />
          </IconButton>
        )}
        <IconButton label={t("smarthome.rooms.edit")} onClick={onEdit}>
          <Pencil className="h-3.5 w-3.5" aria-hidden />
        </IconButton>
      </div>
    </div>
  );
}

function IconButton({
  label,
  onClick,
  busy = false,
  testId,
  children,
}: {
  label: string;
  onClick: () => void;
  busy?: boolean;
  testId?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label={label}
      title={label}
      data-testid={testId}
      className="flex h-7 w-7 items-center justify-center rounded-md border border-border/70 bg-background/60 text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary disabled:opacity-40"
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : children}
    </button>
  );
}

function SuggestionBanner({
  count,
  busy,
  onImport,
}: {
  count: number;
  busy: boolean;
  onImport: () => void;
}) {
  const t = useT();
  return (
    <div
      data-testid="smarthome-room-suggestions"
      className="flex flex-wrap items-center gap-3 rounded-xl border border-primary/30 bg-primary/[0.06] px-4 py-3"
    >
      <Sparkles className="h-4 w-4 shrink-0 text-primary" aria-hidden />
      <p className="min-w-0 flex-1 text-xs text-muted-foreground">
        {t("smarthome.rooms.suggestions").replace("{count}", String(count))}
      </p>
      <button
        type="button"
        onClick={onImport}
        disabled={busy}
        className="flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
      >
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
        {t("smarthome.rooms.import")}
      </button>
    </div>
  );
}

function UnassignedNote({ count }: { count: number }) {
  const t = useT();
  return (
    <p className="text-xs text-muted-foreground">
      {t("smarthome.rooms.unassigned_note").replace("{count}", String(count))}
    </p>
  );
}

function EmptyRooms({ onCreate }: { onCreate: () => void }) {
  const t = useT();
  return (
    <div className="flex flex-col items-center px-8 py-12 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-secondary/50">
        <DoorClosed className="h-5 w-5 text-muted-foreground" aria-hidden />
      </div>
      <h3 className="font-display text-base font-semibold">
        {t("smarthome.rooms.empty_title")}
      </h3>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        {t("smarthome.rooms.empty_body")}
      </p>
      <button
        type="button"
        onClick={onCreate}
        data-testid="smarthome-add-room"
        className="mt-4 flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
      >
        <Plus className="h-4 w-4" aria-hidden />
        {t("smarthome.rooms.add")}
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------- detail */

function RoomDetail({
  room,
  devices,
  busy,
  onBack,
  onEdit,
  onPickDevices,
  onAllOff,
  onCommand,
  onToggleFavorite,
}: {
  room: SmartRoom;
  devices: SmartDevice[];
  busy: boolean;
  onBack: () => void;
  onEdit: () => void;
  onPickDevices: () => void;
  onAllOff: () => void;
  onCommand: RoomsTabProps["onCommand"];
  onToggleFavorite: (deviceId: string) => void;
}) {
  const t = useT();
  const palette = paletteFor(room.color);
  const Icon = iconForRoom(room.icon);
  const activity = roomActivity(devices);
  const favorites = devices.filter((d) => room.favorites.includes(d.id));
  const rest = devices.filter((d) => !room.favorites.includes(d.id));
  const canSwitchOff = devices.some(
    (d) => d.state.on_off === true && d.commands.includes("turn_off"),
  );

  return (
    <div>
      <header
        className={cn("relative overflow-hidden border-b border-border", palette.surface)}
      >
        <div
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-0 bg-gradient-to-b to-transparent transition-opacity duration-500",
            palette.wash,
          )}
          style={{ opacity: 0.3 + activity * 0.7 }}
        />
        <div className="relative flex flex-wrap items-center gap-3 px-6 py-4">
          <button
            type="button"
            onClick={onBack}
            aria-label={t("common.back")}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-border/70 bg-background/60 text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
          </button>
          <Icon className={cn("h-6 w-6", palette.accent)} aria-hidden />
          <div className="min-w-0">
            <h2 className="truncate font-display text-lg font-semibold text-foreground">
              {room.name}
            </h2>
            {room.floor && (
              <p className="text-xs text-muted-foreground">{room.floor}</p>
            )}
          </div>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            {room.temperature !== null && (
              <Reading
                icon={<Thermometer className="h-3.5 w-3.5" aria-hidden />}
                value={`${room.temperature.toFixed(1).replace(/\.0$/, "")}°`}
              />
            )}
            {room.humidity !== null && (
              <Reading value={`${Math.round(room.humidity)}%`} />
            )}
            {canSwitchOff && (
              <button
                type="button"
                onClick={onAllOff}
                disabled={busy}
                data-testid={`smarthome-room-off-${room.id}`}
                className="flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
              >
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Power className="h-3.5 w-3.5" aria-hidden />
                )}
                {t("smarthome.rooms.all_off")}
              </button>
            )}
            <button
              type="button"
              onClick={onEdit}
              className="flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
              {t("smarthome.rooms.edit")}
            </button>
          </div>
        </div>
      </header>

      <div className="space-y-6 p-6">
        {favorites.length > 0 && (
          <section>
            <SectionHeading title={t("smarthome.rooms.favorites")} />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {favorites.map((device) => (
                <FavoriteTile
                  key={device.id}
                  device={device}
                  onCommand={onCommand}
                  onUnfavorite={() => onToggleFavorite(device.id)}
                />
              ))}
            </div>
          </section>
        )}

        {devices.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-6 py-10 text-center">
            <p className="text-sm text-muted-foreground">
              {t("smarthome.rooms.detail_empty")}
            </p>
            <button
              type="button"
              onClick={onPickDevices}
              data-testid="smarthome-pick-devices"
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
            >
              <Plus className="h-4 w-4" aria-hidden />
              {t("smarthome.rooms.add_devices")}
            </button>
          </div>
        ) : (
          groupByKind(rest).map(([kind, kindDevices]) => (
            <section key={kind}>
              <SectionHeading
                title={t(`smarthome.kind.${kind}`)}
                fallback={kind}
                count={kindDevices.length}
              />
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {kindDevices.map((device) => (
                  /* `group` is load-bearing: the star below reveals itself on
                     hover, and without a group ancestor it would only ever
                     appear to keyboard users. */
                  <div key={device.id} className="group relative">
                    <DeviceCard device={device} onCommand={onCommand} showRoom={false} />
                    <button
                      type="button"
                      onClick={() => onToggleFavorite(device.id)}
                      aria-label={t("smarthome.rooms.favorite_add")}
                      title={t("smarthome.rooms.favorite_add")}
                      className="absolute right-2 top-2 rounded-md p-1 text-muted-foreground/50 opacity-0 transition-opacity hover:text-primary focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <Star className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ))
        )}

        {devices.length > 0 && (
          <button
            type="button"
            onClick={onPickDevices}
            data-testid="smarthome-pick-devices"
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
          >
            <Plus className="h-4 w-4" aria-hidden />
            {t("smarthome.rooms.add_devices")}
          </button>
        )}
      </div>
    </div>
  );
}

function SectionHeading({
  title,
  fallback,
  count,
}: {
  title: string;
  fallback?: string;
  count?: number;
}) {
  // `useT` returns the KEY when a locale is missing it; showing the raw key
  // would be a bug on screen, so an unknown device kind falls back to its own
  // name — visibly generic, never broken.
  const label = fallback && title.startsWith("smarthome.") ? fallback : title;
  return (
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
      {label}
      {count !== undefined && (
        <span className="ml-1.5 font-normal tabular-nums opacity-70">{count}</span>
      )}
    </h3>
  );
}

function Reading({ icon, value }: { icon?: React.ReactNode; value: string }) {
  return (
    <span className="flex items-center gap-1 rounded-md border border-border bg-background/60 px-2 py-1 text-xs tabular-nums text-foreground">
      {icon}
      {value}
    </span>
  );
}

/**
 * A favourite, drawn as its own state.
 *
 * The tile FILLS in proportion to what the device is doing — a lamp at 40%
 * brightness is 40% warm, a blind half open is half filled. Reading a room then
 * takes a glance rather than a scan of numbers, which is the whole reason to
 * mark something a favourite in the first place.
 */
function FavoriteTile({
  device,
  onCommand,
  onUnfavorite,
}: {
  device: SmartDevice;
  onCommand: RoomsTabProps["onCommand"];
  onUnfavorite: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const Icon = iconForKind(device.kind);
  const fill = tileFill(device);
  const line = stateLine(device);
  const active = isActive(device);
  const canToggle = device.commands.includes("toggle") || device.commands.includes("turn_on");

  const toggle = async () => {
    if (!canToggle || !device.reachable) return;
    const next = device.state.on_off !== true;
    setBusy(true);
    await onCommand(
      device.id,
      next ? "turn_on" : "turn_off",
      {},
      { on_off: next },
    );
    setBusy(false);
  };

  return (
    <div
      data-testid={`smarthome-favorite-${device.id}`}
      data-active={active ? "true" : "false"}
      className={cn(
        "group relative flex min-h-[6.5rem] flex-col overflow-hidden rounded-xl border transition-colors",
        active ? "border-primary/40" : "border-border",
        !device.reachable && "opacity-55",
      )}
    >
      {/* The fill sits BEHIND the content and grows from the bottom, so the
          tile reads like a vessel rather than a progress bar. */}
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-x-0 bottom-0 transition-all duration-500",
          fill.tone === "warm"
            ? "bg-gradient-to-t from-amber-400/35 to-amber-300/10"
            : fill.tone === "cool"
              ? "bg-gradient-to-t from-sky-400/30 to-sky-300/10"
              : "bg-gradient-to-t from-primary/25 to-primary/5",
        )}
        style={{ height: `${Math.round(fill.level * 100)}%` }}
      />
      <button
        type="button"
        onClick={() => void toggle()}
        disabled={!canToggle || !device.reachable || busy}
        className="relative flex flex-1 flex-col items-start gap-1 p-3 text-left outline-none disabled:cursor-default"
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
        ) : (
          <Icon
            className={cn("h-4 w-4", active ? "text-primary" : "text-muted-foreground")}
            aria-hidden
          />
        )}
        <span className="mt-auto min-w-0 max-w-full">
          <span className="block truncate text-sm font-medium text-foreground">
            {device.name}
          </span>
          <span className="block truncate text-xs text-muted-foreground">
            {!device.reachable
              ? t("smarthome.unreachable")
              : line
                ? renderStateLine(t, line)
                : ""}
          </span>
        </span>
      </button>
      <button
        type="button"
        onClick={onUnfavorite}
        aria-label={t("smarthome.rooms.favorite_remove")}
        title={t("smarthome.rooms.favorite_remove")}
        className="absolute right-1.5 top-1.5 rounded-md p-1 text-primary/70 opacity-0 transition-opacity hover:text-primary focus-visible:opacity-100 group-hover:opacity-100"
      >
        <Star className="h-3.5 w-3.5 fill-current" aria-hidden />
      </button>
    </div>
  );
}
