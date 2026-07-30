/**
 * SourcesPanel tests — the consent gate and what approval actually DOES.
 *
 * Pins that Approve is an explicit POST to the approve route, that a pending
 * source exposes NO sync control at all (the backend would refuse the sync;
 * the UI must not dangle a dead button), and the visibility half: a running
 * import shows its phase and ticking item count, a finished one says how much
 * is stored, a notice is rendered as prominently as an error, and a
 * plugin-bridge card is identifiable as the integration it actually is.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SourcesPanel } from "@/components/ultrawiki/SourcesPanel";
import { useEventStore } from "@/store/events";
import type { UltraWikiSource } from "@/lib/ultrawikiApi";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

function installFetchMock(
  routes: Record<string, (init?: RequestInit) => unknown>,
) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
      for (const prefix of prefixes) {
        if (url.startsWith(prefix)) {
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            json: async () => routes[prefix](init),
          } as Response;
        }
      }
      throw new Error(`unexpected fetch ${url}`);
    },
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function source(overrides: Partial<UltraWikiSource>): UltraWikiSource {
  return {
    id: "src-x",
    connector: "local-folder",
    label: "Some folder",
    consent: "pending",
    enabled: true,
    areas: [],
    counts: {
      captured: 1,
      keyword_indexed: 2,
      embedded: 3,
      distilled: 4,
      failed: 0,
      total: 10,
    },
    sync_state: null,
    last_sync_at: null,
    last_error: null,
    ...overrides,
  };
}

const PENDING = source({ id: "src-pending", label: "Pending folder" });
const APPROVED = source({
  id: "src-approved",
  label: "Approved folder",
  consent: "approved",
  last_sync_at: "2026-07-20T10:00:00Z",
});

beforeEach(() => {
  // Brand discipline: pin an arbitrary assistant name, never the host config.
  useEventStore.getState().setAssistantName("Nova");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SourcesPanel — consent gate", () => {
  it("fires the approve POST for a pending source and reports the change", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/sources/src-pending/approve": () => ({
        source: { ...PENDING, consent: "approved" },
        job_id: "job-1",
        auto_sync: true,
        detail: "Importing everything this source holds.",
      }),
    });
    const onChanged = vi.fn();
    renderWithClient(
      <SourcesPanel sources={[PENDING, APPROVED]} onChanged={onChanged} />,
    );

    fireEvent.click(screen.getByTestId("uw-source-approve-src-pending"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ultrawiki/sources/src-pending/approve",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  it("shows NO sync control on a pending source, but does on an approved one", () => {
    installFetchMock({});
    renderWithClient(
      <SourcesPanel sources={[PENDING, APPROVED]} onChanged={vi.fn()} />,
    );

    // Pending: consent badge, approve button + scope description, no sync.
    expect(
      screen
        .getByTestId("ultrawiki-consent-src-pending")
        .getAttribute("data-consent"),
    ).toBe("pending");
    expect(screen.getByTestId("uw-source-approve-src-pending")).toBeDefined();
    expect(screen.queryByTestId("uw-source-sync-src-pending")).toBeNull();

    // Approved: sync + revoke, no approve.
    expect(screen.getByTestId("uw-source-sync-src-approved")).toBeDefined();
    expect(screen.getByTestId("uw-source-revoke-src-approved")).toBeDefined();
    expect(screen.queryByTestId("uw-source-approve-src-approved")).toBeNull();
  });

  it("starts a sync for an approved source via the sync route", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/sources/src-approved/sync": () => ({
        job_id: "job-1",
        status: "queued",
        source_id: "src-approved",
      }),
    });
    const onChanged = vi.fn();
    renderWithClient(
      <SourcesPanel sources={[APPROVED]} onChanged={onChanged} />,
    );

    fireEvent.click(screen.getByTestId("uw-source-sync-src-approved"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ultrawiki/sources/src-approved/sync",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  it("edits a folder path and exclusions without recreating the source", async () => {
    const editable = source({
      id: "src-editable",
      consent: "approved",
      config: { root: "C:/Notes", exclude: ["archive"] },
    });
    const fetchMock = installFetchMock({
      "/api/ultrawiki/sources/src-editable": () => editable,
    });
    const onChanged = vi.fn();
    renderWithClient(
      <SourcesPanel sources={[editable]} onChanged={onChanged} />,
    );

    fireEvent.click(screen.getByTestId("uw-source-edit-src-editable"));
    fireEvent.change(screen.getByTestId("uw-source-edit-exclude-src-editable"), {
      target: { value: "archive, dist" },
    });
    const form = screen.getByTestId("uw-source-edit-form-src-editable");
    fireEvent.submit(form);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ultrawiki/sources/src-editable",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            config: { root: "C:/Notes", exclude: ["archive", "dist"] },
          }),
        }),
      );
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });
});

describe("SourcesPanel — approve imports everything", () => {
  it("approves with auto-import and reports the started job", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/sources/src-pending/approve": () => ({
        source: { ...PENDING, consent: "approved" },
        job_id: "job-7",
        auto_sync: true,
        detail: "Importing everything this source holds.",
      }),
    });
    renderWithClient(
      <SourcesPanel sources={[PENDING]} onChanged={vi.fn()} />,
    );

    // The button no longer promises a bare flag flip.
    const approve = screen.getByTestId("uw-source-approve-src-pending");
    expect(approve.textContent).toContain("import everything");

    fireEvent.click(approve);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ultrawiki/sources/src-pending/approve",
        expect.objectContaining({ method: "POST" }),
      );
    });
    // No auto_sync=false query: the default IS the import.
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).not.toContain("auto_sync");
  });

  it("shows the running import with its phase, item count and a cancel button", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/jobs/job-7/cancel": () => ({
        job_id: "job-7",
        cancel_requested: true,
      }),
    });
    const importing = source({
      id: "src-importing",
      label: "Importing folder",
      consent: "approved",
      active_job: {
        job_id: "job-7",
        source_id: "src-importing",
        mode: "backfill",
        status: "running",
        phase: "importing",
        started_at: 0,
        ended_at: null,
        chunks: 2,
        items: 412,
        new: 400,
        changed: 10,
        unchanged: 2,
        tombstoned: 0,
        error: "",
      },
    });

    renderWithClient(
      <SourcesPanel sources={[importing]} onChanged={vi.fn()} />,
    );

    const progress = screen.getByTestId("uw-source-progress-src-importing");
    expect(progress).toBeDefined();
    expect(
      screen.getByTestId("uw-source-progress-items-src-importing").textContent,
    ).toContain("412");
    // The phase is the honest "what is it doing", not just "running".
    expect(progress.textContent).toContain("Importing");
    // The reassurance the design asks for: this is a COPY.
    expect(progress.textContent).toContain("COPIED");
    // A running import hides the finished summary and blocks a second sync.
    expect(screen.queryByTestId("uw-source-summary-src-importing")).toBeNull();
    expect(
      screen
        .getByTestId("uw-source-sync-src-importing")
        .hasAttribute("disabled"),
    ).toBe(true);

    fireEvent.click(screen.getByTestId("uw-source-cancel-src-importing"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ultrawiki/jobs/job-7/cancel",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("replaces the ambiguous 'Never synced' with what is actually stored", () => {
    installFetchMock({});
    const done = source({
      id: "src-done",
      consent: "approved",
      counts: {
        captured: 0,
        keyword_indexed: 0,
        embedded: 0,
        distilled: 120,
        failed: 0,
        total: 120,
      },
      last_outcome: {
        finished_at: new Date(Date.now() - 5 * 60_000).toISOString(),
        status: "done",
        mode: "backfill",
        new: 120,
        changed: 0,
        unchanged: 0,
        tombstoned: 0,
      },
    });

    renderWithClient(<SourcesPanel sources={[done]} onChanged={vi.fn()} />);

    const summary = screen.getByTestId("uw-source-summary-src-done");
    expect(summary.textContent).toContain("120");
    expect(summary.textContent).toContain("5 min ago");
  });
});

describe("SourcesPanel — honest error and notice surfaces", () => {
  it("renders a notice as its own prominent box, not as a failure", () => {
    installFetchMock({});
    const noticed = source({
      id: "src-bridge",
      connector: "plugin-bridge",
      label: "GitHub",
      consent: "approved",
      integration_id: "plugin:github",
      last_notice:
        "This integration is connected, but its pull adapter is not built yet - nothing was imported.",
    });

    renderWithClient(<SourcesPanel sources={[noticed]} onChanged={vi.fn()} />);

    const notice = screen.getByTestId("ultrawiki-source-notice-src-bridge");
    expect(notice.textContent).toContain("pull adapter is not built yet");
    // A notice is NOT an error: no alert box, and it says so.
    expect(screen.queryByTestId("ultrawiki-source-error-src-bridge")).toBeNull();
    expect(notice.getAttribute("role")).not.toBe("alert");
  });

  it("renders a sync failure as an alert", () => {
    installFetchMock({});
    const failed = source({
      id: "src-broken",
      consent: "approved",
      last_error: "FileNotFoundError: the vault folder is gone",
    });

    renderWithClient(<SourcesPanel sources={[failed]} onChanged={vi.fn()} />);

    const error = screen.getByTestId("ultrawiki-source-error-src-broken");
    expect(error.getAttribute("role")).toBe("alert");
    expect(error.textContent).toContain("the vault folder is gone");
  });
});

describe("SourcesPanel — bridge sources carry a real identity", () => {
  it("names the card after the integration and says where it comes from", () => {
    installFetchMock({});
    const bridge = source({
      id: "src-gh",
      connector: "plugin-bridge",
      label: "GitHub",
      consent: "approved",
      integration_id: "plugin:github",
    });

    renderWithClient(<SourcesPanel sources={[bridge]} onChanged={vi.fn()} />);

    const card = screen.getByTestId("ultrawiki-source-src-gh");
    expect(card.textContent).toContain("GitHub");
    const subtitle = screen.getByTestId("uw-source-bridge-src-gh");
    expect(subtitle.textContent).toContain("plugins / MCP bridge");
    expect(subtitle.textContent).toContain("plugin:github");
  });

  it("shows the source's brand mark next to its name", () => {
    installFetchMock({});
    const bridge = source({
      id: "src-gh",
      connector: "plugin-bridge",
      label: "GitHub",
      consent: "approved",
      integration_id: "plugin:github",
      brand: "github",
      connector_kind: "bridge",
    });
    const folder = source({
      id: "src-folder",
      connector: "local-folder",
      label: "Notes",
      brand: "folder",
      connector_kind: "builtin",
    });

    renderWithClient(
      <SourcesPanel sources={[bridge, folder]} onChanged={vi.fn()} />,
    );

    // The same resolution the picker uses: a bundled vendor mark for the
    // integration, one of our own icons for a built-in.
    expect(
      screen.getByTestId("uw-source-brand-src-gh").getAttribute("data-brand-tier"),
    ).toBe("asset");
    expect(
      screen
        .getByTestId("uw-source-brand-src-folder")
        .getAttribute("data-brand-tier"),
    ).toBe("neutral");
  });

  it("falls back to a monogram for a source with no roster brand", () => {
    installFetchMock({});
    const orphan = source({
      id: "src-orphan",
      connector: "third-party-connector",
      label: "Zeta archive",
      brand: "",
    });

    renderWithClient(<SourcesPanel sources={[orphan]} onChanged={vi.fn()} />);

    const mark = screen.getByTestId("uw-source-brand-src-orphan");
    expect(mark.getAttribute("data-brand-tier")).toBe("monogram");
    expect(mark.textContent).toBe("Z");
  });

  it("registers a bridge source under the picked integration's label", async () => {
    const created: unknown[] = [];
    const fetchMock = installFetchMock({
      "/api/ultrawiki/areas": () => ({ areas: [], total: 0 }),
      "/api/ultrawiki/connectors": () => ({
        connectors: [
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
        ],
        total: 1,
        builtin: 0,
        bridge: 1,
      }),
      "/api/ultrawiki/bridge/candidates": () => ({
        candidates: [
          {
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
          },
        ],
        total: 1,
        connected: 1,
      }),
      "/api/ultrawiki/sources": (init) => {
        created.push(JSON.parse(String(init?.body ?? "{}")));
        return { id: "src-new" };
      },
    });
    renderWithClient(<SourcesPanel sources={[]} onChanged={vi.fn()} />);

    fireEvent.click(screen.getByTestId("ultrawiki-add-source-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-github")).toBeDefined();
    });
    // Nothing can be created before a source type is picked.
    expect(
      screen.getByTestId("ultrawiki-create-source").hasAttribute("disabled"),
    ).toBe(true);

    fireEvent.click(screen.getByTestId("uw-picker-github"));
    fireEvent.click(screen.getByTestId("ultrawiki-create-source"));

    await waitFor(() => {
      expect(created).toHaveLength(1);
    });
    expect(fetchMock).toHaveBeenCalled();
    // The integration's own name — never the generic bridge label, which made
    // every bridge card look identical.
    expect(created[0]).toMatchObject({
      connector: "plugin-bridge",
      label: "GitHub",
      config: { integration_id: "plugin:github" },
    });
  });
});

describe("SourcesPanel — folders to skip", () => {
  function installFolderPickerMock(created: unknown[]) {
    return installFetchMock({
      "/api/ultrawiki/areas": () => ({ areas: [], total: 0 }),
      "/api/ultrawiki/connectors": () => ({
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
        ],
        total: 1,
        builtin: 1,
        bridge: 0,
      }),
      "/api/ultrawiki/bridge/candidates": () => ({
        candidates: [],
        total: 0,
        connected: 0,
      }),
      "/api/ultrawiki/sources": (init) => {
        created.push(JSON.parse(String(init?.body ?? "{}")));
        return { id: "src-folder" };
      },
    });
  }

  async function pickTheFolderTile() {
    fireEvent.click(screen.getByTestId("ultrawiki-add-source-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-local-folder")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("uw-picker-local-folder"));
  }

  it("sends the skip list as its own config key", async () => {
    const created: unknown[] = [];
    installFolderPickerMock(created);
    renderWithClient(<SourcesPanel sources={[]} onChanged={vi.fn()} />);

    await pickTheFolderTile();
    fireEvent.change(screen.getByTestId("ultrawiki-path-input"), {
      target: { value: "/home/someone/Desktop" },
    });
    fireEvent.change(screen.getByTestId("ultrawiki-exclude-input"), {
      target: { value: "ship-release-work, backups" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-create-source"));

    await waitFor(() => {
      expect(created).toHaveLength(1);
    });
    expect(created[0]).toMatchObject({
      connector: "local-folder",
      config: {
        root: "/home/someone/Desktop",
        exclude: ["ship-release-work", "backups"],
      },
    });
  });

  it("stores no skip list when the field is left alone", async () => {
    const created: unknown[] = [];
    installFolderPickerMock(created);
    renderWithClient(<SourcesPanel sources={[]} onChanged={vi.fn()} />);

    await pickTheFolderTile();
    fireEvent.change(screen.getByTestId("ultrawiki-path-input"), {
      target: { value: "/home/someone/Desktop" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-create-source"));

    await waitFor(() => {
      expect(created).toHaveLength(1);
    });
    // An empty field must not write an empty key the backend then has to
    // interpret — absent means "use the built-in noise list only".
    const body = created[0] as { config: Record<string, unknown> };
    expect("exclude" in body.config).toBe(false);
  });

  it("is offered for a folder source but not for a dropped export", async () => {
    installFolderPickerMock([]);
    renderWithClient(<SourcesPanel sources={[]} onChanged={vi.fn()} />);

    fireEvent.click(screen.getByTestId("ultrawiki-add-source-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-local-folder")).toBeDefined();
    });
    // Nothing before a type is picked: the field belongs to the folder flow.
    expect(screen.queryByTestId("ultrawiki-exclude-input")).toBeNull();

    fireEvent.click(screen.getByTestId("uw-picker-local-folder"));

    expect(screen.getByTestId("ultrawiki-exclude-input")).toBeDefined();
  });
});

describe("SourcesPanel — the dropped-export add flow", () => {
  function installExportPickerMock(
    created: unknown[],
    extra: Record<string, (init?: RequestInit) => unknown> = {},
  ) {
    return installFetchMock({
      "/api/ultrawiki/areas": () => ({ areas: [], total: 0 }),
      "/api/ultrawiki/connectors": () => ({
        connectors: [
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
        ],
        total: 1,
        builtin: 1,
        bridge: 0,
      }),
      "/api/ultrawiki/bridge/candidates": () => ({
        candidates: [],
        total: 0,
        connected: 0,
      }),
      "/api/ultrawiki/sources": (init) => {
        created.push(JSON.parse(String(init?.body ?? "{}")));
        return { id: "src-export" };
      },
      ...extra,
    });
  }

  it("swaps in the upload + preview controls when the export tile is picked", async () => {
    installExportPickerMock([]);
    renderWithClient(<SourcesPanel sources={[]} onChanged={vi.fn()} />);

    fireEvent.click(screen.getByTestId("ultrawiki-add-source-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-export-import")).toBeDefined();
    });
    // A dropped export is not a vault root: the folder field must not appear.
    expect(screen.queryByTestId("ultrawiki-export-panel")).toBeNull();

    fireEvent.click(screen.getByTestId("uw-picker-export-import"));

    expect(screen.getByTestId("ultrawiki-export-panel")).toBeDefined();
    expect(screen.getByTestId("ultrawiki-export-upload-input")).toBeDefined();
    expect(screen.getByTestId("ultrawiki-export-preview")).toBeDefined();
    expect(screen.queryByTestId("ultrawiki-path-input")).toBeNull();
    // Nothing can be registered until there is something to read.
    expect(
      screen.getByTestId("ultrawiki-create-source").hasAttribute("disabled"),
    ).toBe(true);
  });

  it("shows the found-formats report before the source is created", async () => {
    const created: unknown[] = [];
    installExportPickerMock(created, {
      "/api/ultrawiki/export/preview": () => ({
        path: "/drop/takeout.zip",
        exists: true,
        is_dir: false,
        formats: {
          mbox: { files: 2, items_estimate: 3214, exact: true },
          ics: { files: 1, items_estimate: 122, exact: true },
        },
        unknown: [{ extension: ".dll", files: 41 }],
        unreadable: [],
        archives: { files: 1, entries: 9, max_depth: 1, budget_exhausted: false },
        total_bytes: 2048,
        files_seen: 12,
        items_estimate: 3336,
        unknown_files: 41,
        truncated: false,
        notes: [],
      }),
    });
    renderWithClient(<SourcesPanel sources={[]} onChanged={vi.fn()} />);

    fireEvent.click(screen.getByTestId("ultrawiki-add-source-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-export-import")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("uw-picker-export-import"));
    fireEvent.change(screen.getByTestId("ultrawiki-export-path-input"), {
      target: { value: "/drop/takeout.zip" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-export-preview"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-export-report")).toBeDefined();
    });
    const report = screen.getByTestId("ultrawiki-export-report");
    expect(report.textContent).toContain("3,214 mails");
    expect(report.textContent).toContain("122 calendar events");
    expect(report.textContent).toContain("41 unrecognised files skipped");
    // A preview registers nothing — consent still starts at the same gate.
    expect(created).toHaveLength(0);
  });

  it("registers the source with the export path under its own config key", async () => {
    const created: unknown[] = [];
    installExportPickerMock(created);
    renderWithClient(<SourcesPanel sources={[]} onChanged={vi.fn()} />);

    fireEvent.click(screen.getByTestId("ultrawiki-add-source-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("uw-picker-export-import")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("uw-picker-export-import"));
    fireEvent.change(screen.getByTestId("ultrawiki-export-path-input"), {
      target: { value: "/drop/takeout.zip" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-create-source"));

    await waitFor(() => {
      expect(created).toHaveLength(1);
    });
    expect(created[0]).toMatchObject({
      connector: "export-import",
      label: "Export file",
      // `path`, not `root`: an export file is not a folder to walk as a vault.
      config: { path: "/drop/takeout.zip" },
    });
  });
});
