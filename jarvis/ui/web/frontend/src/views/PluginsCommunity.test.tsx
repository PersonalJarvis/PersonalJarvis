import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommunityTab, type CommunityResponse } from "@/views/PluginsCommunity";

const COMMUNITY: CommunityResponse = {
  status: "fresh",
  revision: 3,
  generated_at: "2026-08-12T12:00:00Z",
  plugins: [
    {
      name: "todo-fox",
      valid: true,
      id: "todo-fox",
      display_name: "TodoFox",
      description: "Tasks and reminders from TodoFox",
      category: "Lists & Tasks",
      logo_slug: "todofox",
      publisher: "octocat",
      version: "1.2.0",
      source_url: "https://github.com/PersonalJarvis/marketplace/tree/main/plugins/todo-fox",
      auth: { mode: "pat_paste" },
      mcp_server: { transport: "http", url: "https://mcp.todofox.example/mcp" },
      installed: false,
      seed_conflict: false,
    },
    {
      name: "note-owl",
      valid: true,
      id: "note-owl",
      display_name: "NoteOwl",
      description: "Notes and highlights from NoteOwl",
      category: "Notes",
      logo_slug: "noteowl",
      publisher: "octocat",
      version: "2.0.0",
      auth: { mode: "pat_paste" },
      mcp_server: { transport: "http", url: "https://mcp.noteowl.example/mcp" },
      bundled_skills: ["note-triage", "note-weekly-review"],
      installed: false,
      seed_conflict: false,
    },
    {
      name: "broken-plugin",
      valid: false,
      error: "mcp.json server 'broken-plugin': url must be https://",
      publisher: "someone",
    },
  ],
  skills: [
    {
      name: "three-point-check",
      title: "Three Point Check",
      description: "Summarize any topic in three bullets",
      publisher: "octocat",
      version: "1.0.0",
      categories: ["productivity"],
      source_url: "https://github.com/PersonalJarvis/marketplace",
      raw_url: "https://raw.example/skills/three-point-check/SKILL.md",
      installable: true,
      installed: false,
    },
  ],
};

function installFetchMock(overrides?: Partial<CommunityResponse>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/marketplace/community" && method === "GET") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ...COMMUNITY, ...overrides }),
      } as Response;
    }
    if (url === "/api/marketplace/community/plugins/todo-fox/install") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, plugin: { id: "todo-fox" } }),
      } as Response;
    }
    if (url === "/api/marketplace/community/skills/three-point-check/install") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, name: "three-point-check" }),
      } as Response;
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function renderTab() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <CommunityTab />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CommunityTab", () => {
  it("renders community plugins and skills with the not-reviewed badge", async () => {
    installFetchMock();
    renderTab();
    expect(await screen.findByText("TodoFox")).toBeDefined();
    expect(screen.getByText("Three Point Check")).toBeDefined();
    expect(screen.getAllByText(/not reviewed/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/by octocat/i).length).toBeGreaterThanOrEqual(2);
  });

  it("marks an invalid entry as not installable instead of hiding it", async () => {
    installFetchMock();
    renderTab();
    expect(await screen.findByText("broken-plugin")).toBeDefined();
    expect(screen.getByText(/Not installable/i)).toBeDefined();
  });

  it("shows the consent dialog with the verbatim endpoint before installing", async () => {
    const fetchMock = installFetchMock();
    renderTab();
    await screen.findByText("TodoFox");

    fireEvent.click(screen.getAllByRole("button", { name: "Install" })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Install TodoFox?");
    expect(dialog.textContent).toContain("https://mcp.todofox.example/mcp");
    expect(dialog.textContent?.toLowerCase()).toContain("not reviewed");

    // No install request yet — consent first.
    expect(
      fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST"),
    ).toHaveLength(0);

    const confirm = screen
      .getAllByRole("button", { name: "Install" })
      .find((b) => dialog.contains(b));
    fireEvent.click(confirm!);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/marketplace/community/plugins/todo-fox/install",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("installs a skill only after consent showing the download URL", async () => {
    const fetchMock = installFetchMock();
    renderTab();
    await screen.findByText("Three Point Check");

    const skillRow = screen.getByText("Three Point Check").closest("article")!;
    const installButton = Array.from(
      skillRow.querySelectorAll("button"),
    ).find((b) => b.textContent === "Install");
    fireEvent.click(installButton!);

    // Consent first: the dialog names the exact source URL, nothing posted yet.
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Install Three Point Check?");
    expect(dialog.textContent).toContain(
      "https://raw.example/skills/three-point-check/SKILL.md",
    );
    expect(
      fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST"),
    ).toHaveLength(0);

    const confirm = Array.from(dialog.querySelectorAll("button")).find(
      (b) => b.textContent === "Install",
    );
    fireEvent.click(confirm!);

    // The name is the whole request. Sending a URL from the client would
    // make "install this skill" mean "write whatever that URL serves"; the
    // server re-reads the same index entry the card came from.
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/marketplace/community/skills/three-point-check/install",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const call = fetchMock.mock.calls.find(
      ([u]) => u === "/api/marketplace/community/skills/three-point-check/install",
    )!;
    expect((call[1] as RequestInit).body).toBeUndefined();
  });

  it("names the skills a plugin will write before anything is installed", async () => {
    // Installing a bundle puts FILES in the user's skills folder. A consent
    // dialog that only names the server would be a promise the install
    // breaks, so the skills have to appear by name on this screen.
    installFetchMock();
    renderTab();
    await screen.findByText("NoteOwl");

    const card = screen.getByText("NoteOwl").closest("article")!;
    const installButton = Array.from(card.querySelectorAll("button")).find(
      (b) => b.textContent === "Install",
    );
    fireEvent.click(installButton!);

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Install NoteOwl?");
    expect(dialog.textContent).toContain("skills folder");
    expect(dialog.textContent).toContain("note-triage");
    expect(dialog.textContent).toContain("note-weekly-review");
  });

  it("says honestly when it is showing a stale saved copy", async () => {
    installFetchMock({ status: "stale" });
    renderTab();
    expect(await screen.findByText(/Showing a saved copy/i)).toBeDefined();
  });

  it("explains when the community source is disabled", async () => {
    installFetchMock({ status: "disabled", plugins: [], skills: [] });
    renderTab();
    expect(
      await screen.findByText(/switched off in the configuration/i),
    ).toBeDefined();
  });
});
