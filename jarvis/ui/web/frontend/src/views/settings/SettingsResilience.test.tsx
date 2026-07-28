import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsView } from "@/views/SettingsView";
import { KeybindRow } from "@/views/settings/KeybindRow";
import { SettingsGroupBoundary } from "@/views/settings/SettingsGroupBoundary";

/**
 * Version-skew resilience of the Settings view.
 *
 * The frontend build and the running backend are updated separately, so a
 * payload the UI does not expect is a NORMAL state. On 2026-07-28 a keybinds
 * response that lacked one action made a row set its combo to `undefined`,
 * which reached `comboTokens(...).split("+")` and took the WHOLE Settings view
 * down with "Cannot read properties of undefined (reading 'split')" — the user
 * could not change any setting at all until the app was rebuilt and restarted.
 *
 * Two independent guarantees are pinned here:
 *   1. A row survives a payload missing its action, or missing the maps.
 *   2. Even a group that DOES throw costs only that group; the rest of the
 *      page stays rendered and editable.
 */

afterEach(() => vi.restoreAllMocks());

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const noop = async () => ({
  ok: true,
  action: "call" as const,
  hotkey: "",
  persisted: true,
  restart_required: false,
});

describe("KeybindRow against a skewed backend payload", () => {
  it("renders when the payload does not know the action yet", () => {
    // A backend that predates `dictate`: the maps exist, the entry does not.
    const config = {
      keybinds: { call: "f3+f4" },
      defaults: { call: "f3+f4" },
      suggestions: [],
      restart_required: false,
    };
    render(
      wrap(
        <KeybindRow
          action="dictate"
          label="Dictation"
          config={config}
          loading={false}
          onSave={noop}
        />,
      ),
    );
    expect(screen.getByTestId("combo-field-dictate")).toBeTruthy();
  });

  it("renders when the payload carries no keybind maps at all", () => {
    // A degraded / errored route answering 200 with an empty object.
    const config = {} as never;
    render(
      wrap(
        <KeybindRow
          action="call"
          label="Call"
          config={config}
          loading={false}
          onSave={noop}
        />,
      ),
    );
    expect(screen.getByTestId("combo-field-call")).toBeTruthy();
  });
});

function Exploding(): JSX.Element {
  throw new Error("Cannot read properties of undefined (reading 'split')");
}

describe("SettingsGroupBoundary", () => {
  it("contains a throwing group and leaves its siblings rendered", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      wrap(
        <>
          <SettingsGroupBoundary group="broken">
            <Exploding />
          </SettingsGroupBoundary>
          <SettingsGroupBoundary group="healthy">
            <p>Volume</p>
          </SettingsGroupBoundary>
        </>,
      ),
    );

    // The broken group degrades to its own card, carrying the honest reason...
    expect(screen.getByText("This section could not be loaded")).toBeTruthy();
    expect(
      screen.getByText(/Cannot read properties of undefined/),
    ).toBeTruthy();
    // ...while the neighbouring setting is untouched and still on screen.
    expect(screen.getByText("Volume")).toBeTruthy();
  });
});

describe("SettingsView against an empty backend", () => {
  it("renders the whole page when every route answers {}", async () => {
    // The shape no group expects — the worst case of a version skew.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: true,
            status: 200,
            json: async () => ({}),
            text: async () => "{}",
          }) as unknown as Response,
      ),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(wrap(<SettingsView />));

    // A locale row (identical in every language) proves the page is rendered,
    // and the keybind field proves the row that used to kill it survived.
    await waitFor(() => {
      // One row per language section (interface / recognition / reply).
      expect(screen.getAllByText("Deutsch (German)").length).toBeGreaterThan(0);
    });
    expect(screen.getByTestId("combo-field-call")).toBeTruthy();
  });
});
