/**
 * Connections is a route to take, not a catalogue to read.
 *
 * The properties pinned here are the three failures the old tab had, plus the
 * one this rewrite could newly introduce:
 *
 * * every ecosystem is PRESSABLE and opens something — dead cards that look
 *   pressable were the complaint;
 * * one recommended route is on top, because for nearly every house it is the
 *   whole answer;
 * * the protocols are reachable but folded away, so a beginner is not made to
 *   scroll past KNX to find Philips Hue;
 * * and — the new risk — a freshly built frontend running against the OLDER
 *   Python server (which does not hot-reload) must still show every brand,
 *   never an empty grid.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

const setDemoMode = vi.fn().mockResolvedValue(true);
const useEcosystems = vi.fn();

vi.mock("@/hooks/useSmartHome", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("@/hooks/useSmartHome");
  return {
    ...actual,
    setDemoMode: (...args: unknown[]) => setDemoMode(...args),
    useEcosystems: () => useEcosystems(),
    connectHomeAssistant: vi.fn().mockResolvedValue(undefined),
  };
});

import { ConnectionsTab } from "@/views/smarthome/ConnectionsTab";
import type { Ecosystem, ProviderStatus } from "@/hooks/useSmartHome";

function eco(overrides: Partial<Ecosystem> = {}): Ecosystem {
  return {
    id: "philips_hue",
    display_name: "Philips Hue",
    logo_slug: "philipshue",
    reachability: "planned",
    connection: "local_button_pairing",
    longevity: "permanent",
    note: "Press the button on the bridge.",
    covers: "Hue lights, plugs, sensors",
    tier: "popular",
    logo_color: "0065D3",
    setup_steps: ["Press the round button on the bridge."],
    docs_url: "https://example.invalid/hue",
    ...overrides,
  };
}

const HUB = eco({
  id: "home_assistant",
  display_name: "Home Assistant",
  logo_slug: "homeassistant",
  reachability: "direct",
  connection: "hub",
  tier: "hub",
  logo_color: "18BCF2",
  covers: "Around 2000 integrations",
  setup_steps: ["Open Home Assistant and sign in.", "Create a token."],
});

const KNX = eco({
  id: "knx",
  display_name: "KNX",
  logo_slug: "knx",
  reachability: "via_hub",
  tier: "technical",
  covers: "Wired lighting and heating",
});

const ALEXA = eco({
  id: "amazon_alexa",
  display_name: "Amazon Alexa",
  logo_slug: "amazonalexa",
  reachability: "unavailable",
  connection: "none",
  tier: "popular",
  covers: "—",
  setup_steps: [],
  docs_url: null,
});

function provider(overrides: Partial<ProviderStatus> = {}): ProviderStatus {
  return {
    provider: "home_assistant",
    display_name: "Home Assistant",
    state: "not_configured",
    detail: null,
    device_count: null,
    longevity: "permanent",
    ...overrides,
  };
}

beforeEach(() => {
  useEcosystems.mockReturnValue({ ecosystems: [HUB, eco(), ALEXA, KNX], loading: false });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderTab(providers: ProviderStatus[] = [provider()]) {
  return render(<ConnectionsTab providers={providers} onRefresh={vi.fn()} />);
}

describe("ConnectionsTab", () => {
  it("puts one recommended route on top", () => {
    renderTab();
    expect(screen.getByTestId("smarthome-hub-hero")).toBeTruthy();
    expect(screen.getByText("smarthome.eco.recommended")).toBeTruthy();
  });

  it("opens a detail sheet when an ecosystem is clicked", async () => {
    // The whole complaint: these looked pressable and did nothing at all.
    renderTab();
    fireEvent.click(screen.getByTestId("smarthome-ecosystem-philips_hue"));
    await waitFor(() => expect(screen.getByTestId("ecosystem-sheet")).toBeTruthy());
    expect(screen.getByText("Press the round button on the bridge.")).toBeTruthy();
  });

  it("offers the connect form inside the hub's own sheet", async () => {
    // Sending someone to another screen to paste a token was the dead end.
    renderTab();
    fireEvent.click(screen.getByTestId("smarthome-hub-hero"));
    await waitFor(() => expect(screen.getByTestId("hub-connect-form")).toBeTruthy());
    expect(screen.getByTestId("hub-address")).toBeTruthy();
    expect(screen.getByTestId("hub-token")).toBeTruthy();
  });

  it("keeps the long-lived token off the screen", async () => {
    renderTab();
    fireEvent.click(screen.getByTestId("smarthome-hub-hero"));
    await waitFor(() => expect(screen.getByTestId("hub-token")).toBeTruthy());
    // It is a ten-year credential; it must not sit readable on a shared screen.
    expect(screen.getByTestId("hub-token").getAttribute("type")).toBe("password");
  });

  it("tells a via-hub brand to connect the hub first when it is not connected", async () => {
    renderTab();
    // KNX is technical, so it lives behind the disclosure.
    fireEvent.click(screen.getByTestId("smarthome-show-all-ecosystems"));
    fireEvent.click(screen.getByTestId("smarthome-ecosystem-knx"));
    await waitFor(() => expect(screen.getByTestId("ecosystem-open-hub")).toBeTruthy());
  });

  it("says the hub is ready once it is connected", async () => {
    renderTab([provider({ state: "connected", device_count: 12 })]);
    fireEvent.click(screen.getByTestId("smarthome-show-all-ecosystems"));
    fireEvent.click(screen.getByTestId("smarthome-ecosystem-knx"));
    await waitFor(() => expect(screen.getByText("smarthome.eco.hub_ready")).toBeTruthy());
    expect(screen.queryByTestId("ecosystem-open-hub")).toBeNull();
  });

  it("folds protocols away but keeps them reachable", () => {
    renderTab();
    expect(screen.queryByTestId("smarthome-ecosystem-knx")).toBeNull();
    fireEvent.click(screen.getByTestId("smarthome-show-all-ecosystems"));
    expect(screen.getByTestId("smarthome-ecosystem-knx")).toBeTruthy();
  });

  it("offers no action for an ecosystem nobody can reach", async () => {
    // A disabled button invites a click that can never work; a sentence does not.
    renderTab();
    fireEvent.click(screen.getByTestId("smarthome-ecosystem-amazon_alexa"));
    await waitFor(() => expect(screen.getByTestId("ecosystem-sheet")).toBeTruthy());
    expect(screen.queryByTestId("hub-connect-form")).toBeNull();
    expect(screen.queryByTestId("ecosystem-open-hub")).toBeNull();
  });

  it("shows every brand when the server is too old to send tiers", () => {
    // The desktop app's Python side does not hot-reload, so a new bundle runs
    // against an old server routinely. An empty grid would look far worse than
    // the wall of cards this replaced.
    const untierd = [HUB, eco(), KNX].map((e) => {
      const { tier: _tier, ...rest } = e;
      return rest as Ecosystem;
    });
    useEcosystems.mockReturnValue({ ecosystems: untierd, loading: false });
    renderTab();

    expect(screen.getByTestId("smarthome-hub-hero")).toBeTruthy();
    expect(screen.getByTestId("smarthome-ecosystem-philips_hue")).toBeTruthy();
    expect(screen.getByTestId("smarthome-ecosystem-knx")).toBeTruthy();
  });

  it("still draws a mark when the brand colour is missing", () => {
    const noColour = [HUB, eco({ logo_color: "" })];
    useEcosystems.mockReturnValue({ ecosystems: noColour, loading: false });
    renderTab();
    expect(screen.getByTestId("brand-mark-philips_hue")).toBeTruthy();
  });
});
