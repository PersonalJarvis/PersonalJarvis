/**
 * The rooms tab is now an arrangement the user owns, not a mirror of a hub.
 *
 * What is worth pinning is the behaviour that was IMPOSSIBLE before this
 * existed, plus the two ways a room view quietly lies:
 *
 * * a room can be created with no hardware present at all — the state of a
 *   house someone is setting up, and the state this maintainer is actually in;
 * * the overview and the opened room read the SAME live devices, so a lamp
 *   switched in one is not still burning in the other;
 * * "everything off" is one call to the server, not a loop in the browser, so
 *   the spoken and the tapped version cannot drift apart;
 * * a room the hub has never heard of still renders — the enum-drift rule, on
 *   icons and colours this build has not seen.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match keys, not prose.
  useT: () => (key: string) => key,
}));

import { RoomsTab, type RoomActions } from "@/views/smarthome/RoomsTab";
import type { RoomLayoutPayload, SmartDevice, SmartRoom } from "@/hooks/useSmartHome";

function device(overrides: Partial<SmartDevice> = {}): SmartDevice {
  return {
    id: "demo:light.kitchen",
    provider: "demo",
    native_id: "light.kitchen",
    name: "Kitchen spots",
    kind: "light",
    capabilities: ["on_off"],
    commands: ["turn_on", "turn_off", "toggle"],
    state: { on_off: true },
    room: "Kitchen",
    room_id: "demo:Kitchen",
    reachable: true,
    manufacturer: null,
    model: null,
    unit: null,
    ...overrides,
  };
}

function room(overrides: Partial<SmartRoom> = {}): SmartRoom {
  return {
    id: "kitchen",
    name: "Kitchen",
    icon: "kitchen",
    color: "amber",
    floor: null,
    order: 0,
    provider_rooms: [],
    devices: [],
    excluded_devices: [],
    favorites: [],
    temperature_device: null,
    humidity_device: null,
    device_ids: ["demo:light.kitchen"],
    device_count: 1,
    active_count: 1,
    temperature: 21.5,
    humidity: null,
    ...overrides,
  };
}

function layout(overrides: Partial<RoomLayoutPayload> = {}): RoomLayoutPayload {
  return {
    rooms: [room()],
    unassigned: [],
    suggestions: [],
    icons: ["room", "kitchen", "bedroom"],
    colors: ["slate", "amber", "sky"],
    ...overrides,
  };
}

function actions(overrides: Partial<RoomActions> = {}): RoomActions {
  const noop = vi.fn().mockResolvedValue(null);
  return {
    create: noop,
    update: noop,
    remove: noop,
    reorder: noop,
    importAreas: noop,
    assign: noop,
    unassign: noop,
    toggleFavorite: noop,
    ...overrides,
  };
}

const command = vi.fn().mockResolvedValue({
  ok: true,
  device_id: "demo:light.kitchen",
  command: "turn_off",
  changed: [],
  error: null,
});
const roomCommand = vi.fn().mockResolvedValue(true);

function renderTab(props: Partial<React.ComponentProps<typeof RoomsTab>> = {}) {
  return render(
    <RoomsTab
      devices={[device()]}
      layout={layout()}
      loading={false}
      actions={actions()}
      onCommand={command}
      onRoomCommand={roomCommand}
      {...props}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RoomsTab", () => {
  it("offers room creation even with no hardware and no rooms", () => {
    // The state of a house being set up before the lamps arrive — and the one
    // the old mirror-only tab could only answer with "ask your hub".
    renderTab({ devices: [], layout: layout({ rooms: [] }) });
    expect(screen.getAllByTestId("smarthome-add-room").length).toBeGreaterThan(0);
    expect(screen.getByText("smarthome.rooms.empty_title")).toBeTruthy();
  });

  it("renders a room whose icon and colour this build has never seen", () => {
    // Enum drift: an unknown value must render generically, never drop the
    // room off the screen where nobody can switch its lights off.
    renderTab({
      layout: layout({
        rooms: [room({ icon: "wine_cellar", color: "chartreuse" })],
      }),
    });
    expect(screen.getByTestId("smarthome-room-kitchen")).toBeTruthy();
    expect(screen.getByText("Kitchen")).toBeTruthy();
  });

  it("sends everything-off to the server rather than looping in the browser", async () => {
    // The loop belongs where the assistant can reach it too: spoken and tapped
    // must mean the same thing.
    renderTab();
    fireEvent.click(screen.getByTestId("smarthome-room-off-kitchen"));
    await waitFor(() => expect(roomCommand).toHaveBeenCalledWith("kitchen", "turn_off"));
    expect(command).not.toHaveBeenCalled();
  });

  it("hides everything-off when nothing in the room is on", () => {
    // A button that is usually a no-op teaches people to stop trusting buttons.
    renderTab({
      devices: [device({ state: { on_off: false } })],
      layout: layout({ rooms: [room({ active_count: 0 })] }),
    });
    expect(screen.queryByTestId("smarthome-room-off-kitchen")).toBeNull();
  });

  it("opens a room and shows its devices", () => {
    renderTab();
    fireEvent.click(screen.getByText("Kitchen"));
    expect(screen.getByTestId("smarthome-device-demo:light.kitchen")).toBeTruthy();
    expect(screen.getByTestId("smarthome-pick-devices")).toBeTruthy();
  });

  it("keeps the opened room in step with the live device list", () => {
    // The open room is held BY ID; holding the object would freeze it at the
    // state it had when opened, and the lamp would stay lit after being
    // switched off from anywhere else.
    const { rerender } = renderTab();
    fireEvent.click(screen.getByText("Kitchen"));
    expect(
      screen.getByTestId("smarthome-device-demo:light.kitchen").dataset.active,
    ).toBe("true");

    rerender(
      <RoomsTab
        devices={[device({ state: { on_off: false } })]}
        layout={layout({ rooms: [room({ active_count: 0 })] })}
        loading={false}
        actions={actions()}
        onCommand={command}
        onRoomCommand={roomCommand}
      />,
    );
    expect(
      screen.getByTestId("smarthome-device-demo:light.kitchen").dataset.active,
    ).toBe("false");
  });

  it("offers the hub's areas instead of adopting them silently", async () => {
    const importAreas = vi.fn().mockResolvedValue(null);
    renderTab({
      layout: layout({
        suggestions: [
          { id: "ha:bath", name: "Bathroom", provider: "ha", device_count: 2 },
        ],
      }),
      actions: actions({ importAreas }),
    });
    expect(screen.getByTestId("smarthome-room-suggestions")).toBeTruthy();
    expect(importAreas).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("smarthome.rooms.import"));
    await waitFor(() => expect(importAreas).toHaveBeenCalledTimes(1));
  });

  it("says so when devices belong to no room, rather than hiding them", () => {
    renderTab({ layout: layout({ unassigned: ["demo:light.hall"] }) });
    expect(screen.getByText(/smarthome.rooms.unassigned_note/)).toBeTruthy();
  });

  it("draws favourites as their own tile, above the rest", () => {
    renderTab({
      layout: layout({ rooms: [room({ favorites: ["demo:light.kitchen"] })] }),
    });
    fireEvent.click(screen.getByText("Kitchen"));
    expect(screen.getByTestId("smarthome-favorite-demo:light.kitchen")).toBeTruthy();
    // A favourite is not ALSO listed below — one device, one tile.
    expect(screen.queryByTestId("smarthome-device-demo:light.kitchen")).toBeNull();
  });

  it("opens the editor for an existing room without leaving the tab", () => {
    renderTab();
    fireEvent.click(screen.getByLabelText("smarthome.rooms.edit"));
    expect(screen.getByTestId("room-editor-dialog")).toBeTruthy();
    expect(screen.getByText("smarthome.rooms.edit_title")).toBeTruthy();
  });
});
