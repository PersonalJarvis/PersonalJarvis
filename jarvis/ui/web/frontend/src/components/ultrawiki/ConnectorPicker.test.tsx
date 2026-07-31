/**
 * ConnectorPicker tests — the picker is a CATALOG, not a registry viewer.
 *
 * Pins the four properties the roster exists to guarantee: only curated entries
 * render (a connected tool the roster does not name stays invisible), each tile
 * carries a real brand mark with a monogram fallback rather than a broken
 * image, a curated integration that is not connected is shown but refuses to be
 * added, and the honest status badge is never silently upgraded.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConnectorPicker } from "@/components/ultrawiki/ConnectorPicker";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

const ROSTER = {
  connectors: [
    {
      id: "local-folder",
      kind: "builtin",
      label: "Local folder",
      label_key: "ultrawiki.sources.connector_local_folder",
      brand: "folder",
      status: "available",
      description_key: "ultrawiki.connectors.local_folder",
      connector: "local-folder",
      integration_id: "",
    },
    {
      id: "export-import",
      kind: "builtin",
      label: "Export file import",
      label_key: "ultrawiki.sources.connector_export_import",
      brand: "archive",
      status: "available",
      description_key: "ultrawiki.connectors.export_import",
      connector: "export-import",
      integration_id: "",
    },
    {
      id: "github",
      kind: "bridge",
      label: "GitHub",
      label_key: "",
      brand: "github",
      status: "adapter_pending",
      description_key: "ultrawiki.connectors.github",
      connector: "plugin-bridge",
      integration_id: "plugin:github",
    },
    {
      id: "notion",
      kind: "bridge",
      label: "Notion",
      label_key: "",
      brand: "notion",
      status: "adapter_pending",
      description_key: "ultrawiki.connectors.notion",
      connector: "plugin-bridge",
      integration_id: "plugin:notion",
    },
    // A curated entry whose brand mark is NOT bundled — the monogram path.
    {
      id: "acme_notes",
      kind: "bridge",
      label: "Acme Notes",
      label_key: "",
      brand: "acme_notes",
      status: "adapter_pending",
      description_key: "ultrawiki.connectors.acme_notes",
      connector: "plugin-bridge",
      integration_id: "plugin:acme_notes",
    },
  ],
  total: 5,
  builtin: 2,
  bridge: 3,
};

function candidate(overrides: Record<string, unknown>) {
  return {
    id: "plugin:github",
    kind: "plugin",
    label: "GitHub",
    detail: "connected marketplace plugin; pull adapter pending",
    catalog_id: "github",
    brand: "github",
    connector_kind: "bridge",
    description_key: "ultrawiki.connectors.github",
    status: "adapter_pending",
    connected: true,
    has_pull_adapter: false,
    ...overrides,
  };
}

function installFetchMock(routes: Record<string, () => unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const prefix of prefixes) {
      if (url.startsWith(prefix)) {
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => routes[prefix](),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function mountPicker(
  candidates: unknown[],
  onSelect = vi.fn(),
  selectedKey = "",
) {
  installFetchMock({
    "/api/ultrawiki/connectors": () => ROSTER,
    "/api/ultrawiki/bridge/candidates": () => ({
      candidates,
      total: candidates.length,
      connected: candidates.filter(
        (c) => (c as { connected?: boolean }).connected,
      ).length,
    }),
  });
  renderWithClient(
    <ConnectorPicker selectedKey={selectedKey} onSelect={onSelect} />,
  );
  return onSelect;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ConnectorPicker — only the curated roster renders", () => {
  it("shows roster entries and hides a connected tool the roster does not name", async () => {
    mountPicker([
      candidate({}),
      // Connected, and deliberately absent from the roster: the exact class of
      // row the old raw-dump picker used to display.
      candidate({
        id: "mcp:grace-plug-in",
        catalog_id: "grace_plug_in",
        label: "grace plug in mcp",
        brand: "",
      }),
    ]);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-github")).toBeDefined();
    });
    expect(screen.getByTestId("uw-picker-local-folder")).toBeDefined();
    expect(screen.queryByTestId("uw-picker-grace_plug_in")).toBeNull();
    expect(screen.queryByText("grace plug in mcp")).toBeNull();
  });

  it("renders every tile with a brand mark, falling back to a monogram", async () => {
    mountPicker([candidate({})]);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-brand-github")).toBeDefined();
    });
    // A bundled vendor mark, our own neutral icon, and the monogram fallback —
    // never a broken image, never a blank tile.
    expect(
      screen.getByTestId("uw-picker-brand-github").getAttribute("data-brand-tier"),
    ).toBe("asset");
    expect(
      screen
        .getByTestId("uw-picker-brand-local-folder")
        .getAttribute("data-brand-tier"),
    ).toBe("neutral");
    const monogram = screen.getByTestId("uw-picker-brand-acme_notes");
    expect(monogram.getAttribute("data-brand-tier")).toBe("monogram");
    expect(monogram.textContent).toBe("A");
  });

  it("offers the export on-ramp as a selectable, vendor-free built-in", async () => {
    const onSelect = mountPicker([candidate({})]);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-export-import")).toBeDefined();
    });
    const tile = screen.getByTestId("uw-picker-export-import");
    // It belongs to no vendor: our own glyph, never a borrowed mark.
    expect(
      screen
        .getByTestId("uw-picker-brand-export-import")
        .getAttribute("data-brand-tier"),
    ).toBe("neutral");
    // A built-in is always "connected" — there is nothing to connect.
    expect(tile.getAttribute("data-connected")).toBe("true");
    expect(
      screen
        .getByTestId("uw-picker-badge-export-import")
        .getAttribute("data-status"),
    ).toBe("available");

    fireEvent.click(tile);
    expect(onSelect.mock.calls[0][0]).toMatchObject({
      key: "export-import",
      connector: "export-import",
      integrationId: "",
      connected: true,
    });
  });
});

describe("ConnectorPicker — honest status", () => {
  it("flags a curated integration that is not connected and refuses to add it", async () => {
    const onSelect = mountPicker([candidate({})]);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-notion")).toBeDefined();
    });
    const tile = screen.getByTestId("uw-picker-notion");
    expect(tile.getAttribute("data-connected")).toBe("false");
    expect(tile.hasAttribute("disabled")).toBe(true);
    expect(
      screen.getByTestId("uw-picker-badge-notion").getAttribute("data-status"),
    ).toBe("not_connected");
    // It says where to go instead of leaving a dead tile unexplained.
    expect(screen.getByTestId("uw-picker-hint-notion").textContent).toContain(
      "Plugins",
    );

    fireEvent.click(tile);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("marks a connected integration whose reader is not built yet", async () => {
    mountPicker([candidate({})]);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-badge-github")).toBeDefined();
    });
    expect(
      screen.getByTestId("uw-picker-badge-github").getAttribute("data-status"),
    ).toBe("adapter_pending");
  });

  it("shows a registered reader as ready to import", async () => {
    mountPicker([candidate({ status: "available", has_pull_adapter: true })]);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-badge-github")).toBeDefined();
    });
    expect(
      screen.getByTestId("uw-picker-badge-github").getAttribute("data-status"),
    ).toBe("available");
  });

  it("selects a connected entry with the candidate's own integration id", async () => {
    const onSelect = mountPicker([
      // A user's own MCP server for the same product: the source must be wired
      // to THAT id, not to the roster's default plugin id.
      candidate({ id: "mcp:github", kind: "mcp" }),
    ]);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-github")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("uw-picker-github"));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({
      key: "github",
      connector: "plugin-bridge",
      integrationId: "mcp:github",
      label: "GitHub",
      connected: true,
    });
  });
});

describe("ConnectorPicker — an unanswerable question is not guessed at", () => {
  it("withholds bridge tiles when the connection check fails", async () => {
    // No route for the candidates call: the mock rejects it like a dead
    // backend would.
    installFetchMock({ "/api/ultrawiki/connectors": () => ROSTER });
    renderWithClient(<ConnectorPicker selectedKey="" onSelect={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-candidates-failed")).toBeDefined();
    });
    // Built-ins are unaffected — the picker stays usable.
    expect(screen.getByTestId("uw-picker-local-folder")).toBeDefined();
    // Claiming "not connected" about an app we could not ask would send
    // someone reconnecting a healthy integration.
    expect(screen.queryByTestId("uw-picker-github")).toBeNull();
  });
});
