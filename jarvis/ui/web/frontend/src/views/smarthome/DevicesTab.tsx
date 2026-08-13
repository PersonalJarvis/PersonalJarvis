/**
 * Every device in the house, grouped by room.
 *
 * The empty state does real work here. A fresh install has no hub, and a blank
 * grid teaches nobody anything — so it offers the two moves that exist at that
 * moment: connect a hub, or switch on the simulated home and see what the
 * section does before buying anything.
 */
import { useMemo, useState } from "react";
import { House, Loader2, RefreshCw, Search } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { CommandOutcome, SmartDevice } from "@/hooks/useSmartHome";
import { DeviceCard } from "@/views/smarthome/DeviceCard";
import { groupByRoom } from "@/views/smarthome/deviceMeta";

export interface DevicesTabProps {
  devices: SmartDevice[];
  loading: boolean;
  connected: boolean;
  onCommand: (
    deviceId: string,
    command: string,
    args?: Record<string, unknown>,
    optimistic?: Record<string, unknown>,
  ) => Promise<CommandOutcome>;
  onRefresh: () => void;
}

/** Filter chips, in the order a house is usually thought about. */
const KIND_FILTERS = [
  { id: "all", labelKey: "smarthome.filter.all" },
  { id: "light", labelKey: "smarthome.filter.lights" },
  { id: "climate", labelKey: "smarthome.filter.climate" },
  { id: "cover", labelKey: "smarthome.filter.covers" },
  { id: "lock", labelKey: "smarthome.filter.locks" },
  { id: "sensor", labelKey: "smarthome.filter.sensors" },
  { id: "scene", labelKey: "smarthome.filter.scenes" },
] as const;

export function DevicesTab({
  devices,
  loading,
  connected,
  onCommand,
  onRefresh,
}: DevicesTabProps) {
  const t = useT();
  const setActive = useEventStore((s) => s.setActiveSection);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<string>("all");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return devices.filter((device) => {
      if (kind !== "all") {
        // "switch" and "outlet" both belong under Lights in a person's head far
        // less than under their own chip, so the mapping stays literal — except
        // sensors, where the binary kind is the same idea to a user.
        const matchesKind =
          kind === "sensor"
            ? device.kind === "sensor" || device.kind === "binary_sensor"
            : device.kind === kind;
        if (!matchesKind) return false;
      }
      if (!needle) return true;
      return (
        device.name.toLowerCase().includes(needle) ||
        (device.room ?? "").toLowerCase().includes(needle)
      );
    });
  }, [devices, query, kind]);

  const groups = useMemo(() => groupByRoom(filtered), [filtered]);

  if (loading && devices.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  if (devices.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-md text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-secondary/50">
            <House className="h-5 w-5 text-muted-foreground" aria-hidden />
          </div>
          <h3 className="font-display text-base font-semibold">
            {connected ? t("smarthome.empty.no_devices") : t("smarthome.empty.title")}
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">
            {connected
              ? t("smarthome.empty.no_devices_body")
              : t("smarthome.empty.body")}
          </p>
          <button
            type="button"
            onClick={() => setActive("smarthome-connections")}
            className="mt-5 rounded-lg border border-primary/40 bg-primary/10 px-4 py-2 text-sm font-medium text-primary transition-colors hover:bg-primary/20"
          >
            {t("smarthome.empty.cta")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-border px-6 py-3">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("smarthome.search_placeholder")}
            aria-label={t("smarthome.search_placeholder")}
            className="jarvis-input-surface w-full rounded-md border border-border py-1.5 pl-8 pr-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary/40"
          />
        </div>
        <div className="flex items-center gap-1">
          {KIND_FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              onClick={() => setKind(filter.id)}
              aria-pressed={kind === filter.id}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                kind === filter.id
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(filter.labelKey)}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          aria-label={t("smarthome.refresh")}
          title={t("smarthome.refresh")}
          className="flex h-7 w-7 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-6 p-6">
          {groups.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("smarthome.no_matches")}</p>
          )}
          {groups.map(([room, roomDevices]) => (
            <section key={room ?? "__none__"}>
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {room ?? t("smarthome.no_room")}
                <span className="ml-2 font-normal normal-case tracking-normal text-muted-foreground/60">
                  {roomDevices.length}
                </span>
              </h3>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {roomDevices.map((device) => (
                  <DeviceCard
                    key={device.id}
                    device={device}
                    onCommand={onCommand}
                    showRoom={false}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
