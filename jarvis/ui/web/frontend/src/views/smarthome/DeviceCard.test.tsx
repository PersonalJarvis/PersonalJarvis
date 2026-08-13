/**
 * The card draws controls from what the BACKEND says a device can do.
 *
 * That is the property worth pinning: a dimmable lamp and a filament bulb are
 * the same `kind`, and only the command list separates them. If the card ever
 * starts branching on kind instead, a non-dimmable bulb grows a slider that
 * sends a command its hub will refuse — a control that is a lie.
 *
 * The optimistic path is pinned for the same reason: a switch that visibly lags
 * behind the finger is the single thing that makes a smart-home surface feel
 * broken, and a refusal that does NOT snap back is worse than the lag.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match keys, not prose.
  useT: () => (key: string) => key,
}));

import { DeviceCard } from "@/views/smarthome/DeviceCard";
import type { SmartDevice } from "@/hooks/useSmartHome";

function device(overrides: Partial<SmartDevice> = {}): SmartDevice {
  return {
    id: "demo:light.kitchen",
    provider: "demo",
    native_id: "light.kitchen",
    name: "Kitchen spots",
    kind: "light",
    capabilities: ["on_off", "brightness"],
    commands: ["turn_on", "turn_off", "toggle", "set_brightness"],
    state: { on_off: false, brightness: 60 },
    room: "Kitchen",
    room_id: "demo:Kitchen",
    reachable: true,
    manufacturer: null,
    model: null,
    unit: null,
    ...overrides,
  };
}

const ok = vi.fn().mockResolvedValue({
  ok: true,
  device_id: "demo:light.kitchen",
  command: "turn_on",
  changed: [],
  error: null,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DeviceCard", () => {
  it("offers a brightness slider only when the device advertises the command", () => {
    const { rerender } = render(<DeviceCard device={device()} onCommand={ok} />);
    expect(screen.getByLabelText("smarthome.control.brightness")).toBeTruthy();

    rerender(
      <DeviceCard
        device={device({ capabilities: ["on_off"], commands: ["turn_on", "turn_off"] })}
        onCommand={ok}
      />,
    );
    expect(screen.queryByLabelText("smarthome.control.brightness")).toBeNull();
  });

  it("sends turn_on with an optimistic state when the switch is flipped", async () => {
    render(<DeviceCard device={device()} onCommand={ok} />);
    fireEvent.click(screen.getByLabelText("Kitchen spots"));
    await waitFor(() =>
      expect(ok).toHaveBeenCalledWith(
        "demo:light.kitchen",
        "turn_on",
        {},
        { on_off: true },
        false,
      ),
    );
  });

  it("shows the platform's refusal instead of swallowing it", async () => {
    const refuse = vi.fn().mockResolvedValue({
      ok: false,
      device_id: "demo:light.kitchen",
      command: "turn_on",
      changed: [],
      error: "That lamp is not answering.",
    });
    render(<DeviceCard device={device()} onCommand={refuse} />);
    fireEvent.click(screen.getByLabelText("Kitchen spots"));
    await waitFor(() =>
      expect(
        screen.getByTestId("smarthome-refusal-demo:light.kitchen").textContent,
      ).toContain("not answering"),
    );
  });

  it("disables an unreachable device rather than removing its controls", () => {
    // Disabled, not gone: a card that loses its switch when the hardware drops
    // off makes the whole grid reflow, and the empty slot reads as "this device
    // changed" rather than "this device is not answering right now".
    render(<DeviceCard device={device({ reachable: false })} onCommand={ok} />);
    expect(screen.getByLabelText("Kitchen spots").hasAttribute("disabled")).toBe(true);
    // The sliders DO go, because a control that cannot be committed is a lie.
    expect(screen.queryByLabelText("smarthome.control.brightness")).toBeNull();
    expect(
      screen.getByTestId("smarthome-device-demo:light.kitchen").dataset.active,
    ).toBe("false");
  });

  it("does not read a locked door as active", () => {
    render(
      <DeviceCard
        device={device({
          id: "demo:lock.front_door",
          kind: "lock",
          capabilities: ["lock"],
          commands: ["lock", "unlock"],
          state: { locked: true },
        })}
        onCommand={ok}
      />,
    );
    expect(
      screen.getByTestId("smarthome-device-demo:lock.front_door").dataset.active,
    ).toBe("false");
  });

  it("turns a server confirmation request into a second, deliberate click", async () => {
    // The list of irreversible commands lives on the SERVER; the card learns
    // about it only from `requires_confirmation`. This is the whole reason the
    // frontend keeps no copy of that list to drift out of sync.
    const gate = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        device_id: "demo:lock.front_door",
        command: "unlock",
        changed: [],
        error: "needs confirmation",
        requires_confirmation: true,
      })
      .mockResolvedValueOnce({
        ok: true,
        device_id: "demo:lock.front_door",
        command: "unlock",
        changed: [],
        error: null,
      });

    render(
      <DeviceCard
        device={device({
          id: "demo:lock.front_door",
          kind: "lock",
          capabilities: ["lock"],
          commands: ["lock", "unlock"],
          state: { locked: true },
        })}
        onCommand={gate}
      />,
    );

    const unlock = screen.getByTestId("smarthome-unlock-demo:lock.front_door");
    fireEvent.click(unlock);
    // The first click asks; it must NOT be reported as a plain failure.
    await waitFor(() => expect(unlock.textContent).toBe("smarthome.control.confirm"));
    expect(screen.queryByTestId("smarthome-refusal-demo:lock.front_door")).toBeNull();
    expect(gate).toHaveBeenLastCalledWith(
      "demo:lock.front_door",
      "unlock",
      {},
      { locked: false },
      false,
    );

    fireEvent.click(unlock);
    await waitFor(() =>
      expect(gate).toHaveBeenLastCalledWith(
        "demo:lock.front_door",
        "unlock",
        {},
        { locked: false },
        true,
      ),
    );
  });

  it("renders an unknown device kind rather than dropping it", () => {
    render(
      <DeviceCard
        device={device({
          id: "demo:doorbell.x",
          kind: "quantum_teapot",
          capabilities: ["read_only"],
          commands: [],
          state: { value: "brewing" },
        })}
        onCommand={ok}
      />,
    );
    expect(screen.getByTestId("smarthome-device-demo:doorbell.x")).toBeTruthy();
  });
});
