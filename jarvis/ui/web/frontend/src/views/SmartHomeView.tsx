import { House } from "lucide-react";

import { SectionTabBar, type SectionTab } from "@/components/layout/SectionTabBar";
import { ViewHeader } from "@/views/ChatsView";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { useSmartHome } from "@/hooks/useSmartHome";
import { ConnectionsTab } from "@/views/smarthome/ConnectionsTab";
import { DevicesTab } from "@/views/smarthome/DevicesTab";
import { RoomsTab } from "@/views/smarthome/RoomsTab";

/**
 * Smart Home — the house as its own section, not a plugin about the house.
 *
 *   [ Devices ] [ Rooms ] [ Connections ]
 *
 * Same merged-section pattern as VoiceHubView / ExtensionsView: the active
 * sidebar section id IS the tab state, so a voice deep-link lands on the right
 * tab with no extra routing, and the section header is drawn ONCE here rather
 * than by each tab (two stacked bordered bands read as a rendering fault).
 *
 * The data is fetched ONCE at this level and handed down. Each tab renders the
 * same house from a different angle, so three independent fetches would make a
 * hub on someone's home network answer the same question three times — and the
 * three tabs would then disagree about whether a lamp is on.
 */

const TABS = [
  { id: "smarthome", labelKey: "smarthome.tab.devices" },
  { id: "smarthome-rooms", labelKey: "smarthome.tab.rooms" },
  { id: "smarthome-connections", labelKey: "smarthome.tab.connections" },
] as const satisfies readonly SectionTab[];

export function SmartHomeView() {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const {
    devices,
    layout,
    providers,
    connected,
    loading,
    error,
    refetch,
    execute,
    executeRoom,
    roomActions,
  } = useSmartHome();

  // The router only mounts us for the three ids above; anything else is
  // unexpected — fall back to Devices defensively.
  const current = TABS.some((tab) => tab.id === active) ? active : "smarthome";

  const activeCount = devices.filter((d) => d.state?.on_off === true).length;
  const subtitle = connected
    ? t("smarthome.subtitle_connected")
        .replace("{devices}", String(devices.length))
        .replace("{active}", String(activeCount))
    : t("smarthome.subtitle_idle");

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<House className="h-4 w-4 text-primary" />}
        title={t("nav.smarthome")}
        subtitle={subtitle}
      />
      <SectionTabBar tabs={TABS} />
      {error && (
        <p
          data-testid="smarthome-error"
          className="border-b border-border px-6 py-2 text-xs text-destructive"
        >
          {error}
        </p>
      )}
      {/* min-h-0 is load-bearing: without it a flex child with its own scroll
          container grows past the viewport instead of scrolling. */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {current === "smarthome" && (
          <DevicesTab
            devices={devices}
            loading={loading}
            connected={connected}
            onCommand={execute}
            onRefresh={() => void refetch(true)}
          />
        )}
        {current === "smarthome-rooms" && (
          <RoomsTab
            devices={devices}
            layout={layout}
            loading={loading}
            actions={roomActions}
            onCommand={execute}
            onRoomCommand={executeRoom}
          />
        )}
        {current === "smarthome-connections" && (
          <ConnectionsTab providers={providers} onRefresh={() => void refetch(true)} />
        )}
      </div>
    </div>
  );
}
