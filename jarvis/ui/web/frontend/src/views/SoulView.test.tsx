/**
 * Component tests for the SOUL.md section — the assistant's own profile.
 *
 * What these pin: the page is called SOUL.md (never "{name}.md", which a live
 * call confused with the character file), it lists every file the assistant
 * reads, its character comes from SOUL.md as rows, explicit memories say so,
 * a note can be forgotten only while the learning loop runs, and the files
 * open in place — SOUL.md and the instructions as editors that save to their
 * own endpoints.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SoulView } from "@/views/SoulView";
import type { SoulProfile } from "@/views/soul/api";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function profile(overrides: Partial<SoulProfile> = {}): SoulProfile {
  return {
    name: "George",
    named: true,
    product: "Personal Jarvis",
    wake_phrase: "Hey George",
    learning: true,
    files: [
      {
        id: "soul",
        filename: "SOUL.md",
        exists: true,
        editable: true,
        updated_ms: Date.now(),
        chars: 120,
        content: "# My own persona\n\n## Who I am\n\n- **Name:** George\n- **Vibe:** Dry and direct.\n",
        character: {
          who: [
            { label: "Role", text: "Personal voice assistant." },
            { label: "Vibe", text: "Dry and direct." },
          ],
          tone: [{ label: "", text: "No corporate-speak." }],
          limits: [{ label: "", text: "No made-up facts." }],
        },
        learned: [{ id: "s1", text: "Answers in short spoken sentences.", importance: 5, origin: "review", explicit: false }],
        name_in_file: "George",
      },
      {
        id: "instructions",
        filename: "George.md",
        exists: true,
        editable: true,
        updated_ms: Date.now(),
        chars: 21,
        content: "Call me Ruben, please.",
        template: "# How George should work with me\n",
      },
      {
        id: "memory",
        filename: "MEMORY.md",
        exists: true,
        editable: false,
        updated_ms: Date.now(),
        chars: 60,
        entries: [
          {
            id: "m1",
            text: "2026-10-02 (asked to remember): The sister is called Lena.",
            importance: 10,
            origin: "user",
            explicit: true,
          },
        ],
      },
      { id: "user", filename: "USER.md", exists: false, editable: false, updated_ms: null, chars: 0, entries: [] },
    ],
    activity: [
      {
        ts: "2026-10-02T10:00:10+0200",
        source: "remember tool",
        target: "memory",
        operation: "add",
        text: "2026-10-02 (asked to remember): The sister is called Lena.",
        evidence: "merk dir das",
      },
    ],
    ...overrides,
  };
}

type Handler = (init?: RequestInit) => { status?: number; body: unknown };

function installFetch(routes: Record<string, Handler>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected fetch ${key}`);
    const { status = 200, body } = handler(init);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SoulView", () => {
  it("is called SOUL.md and lists every file the assistant reads", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<SoulView />);

    expect(await screen.findByTestId("soul-hero")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("SOUL.md");
    expect(screen.getByText("Hey George")).toBeTruthy();
    expect(screen.getByTestId("soul-vibe").textContent).toBe("Dry and direct.");

    const files = screen.getByTestId("soul-files");
    for (const name of ["SOUL.md", "George.md", "MEMORY.md", "USER.md"]) {
      expect(within(files).getByText(name)).toBeTruthy();
    }
    expect(within(screen.getByTestId("soul-file-user")).getByText(/Not created yet/)).toBeTruthy();
  });

  it("shows the character from SOUL.md and an explicit memory as one", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<SoulView />);

    const character = await screen.findByTestId("soul-character");
    expect(within(character).getByText("Personal voice assistant.")).toBeTruthy();
    expect(within(character).getByText("No corporate-speak.")).toBeTruthy();

    const notes = screen.getByTestId("soul-notes");
    expect(within(notes).getByText("Answers in short spoken sentences.")).toBeTruthy();
    fireEvent.click(within(notes).getByRole("button", { name: /^Memory/ }));
    expect(within(notes).getByText("The sister is called Lena.")).toBeTruthy();
    expect(within(notes).getByText("You asked")).toBeTruthy();

    const activity = screen.getByTestId("soul-activity");
    expect(within(activity).getByText("“merk dir das”")).toBeTruthy();
  });

  it("forgets a note through the soul endpoint", async () => {
    const calls = installFetch({
      "GET /api/soul": () => ({ body: profile() }),
      "DELETE /api/soul/entries/soul/s1": () => ({
        body: profile({ files: profile().files.map((f) => (f.id === "soul" ? { ...f, learned: [] } : f)) }),
      }),
    });
    renderWithClient(<SoulView />);

    const notes = await screen.findByTestId("soul-notes");
    fireEvent.click(within(notes).getByRole("button", { name: /Forget: Answers/ }));
    await waitFor(() => expect(within(notes).getByTestId("soul-notes-empty-soul")).toBeTruthy());
    expect(calls.some((c) => c.url === "/api/soul/entries/soul/s1" && c.init?.method === "DELETE")).toBe(true);
  });

  it("cannot forget while the learning loop is off", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile({ learning: false }) }) });
    renderWithClient(<SoulView />);

    const notes = await screen.findByTestId("soul-notes");
    const forget = within(notes).getByRole("button", { name: /Forget: Answers/ }) as HTMLButtonElement;
    expect(forget.disabled).toBe(true);
    expect(screen.getByTestId("soul-learning").textContent).toBe("Learning paused");
  });

  it("opens SOUL.md as an editor that saves to /api/soul/file", async () => {
    const calls = installFetch({
      "GET /api/soul": () => ({ body: profile() }),
      "PUT /api/soul/file": () => ({ body: profile() }),
    });
    renderWithClient(<SoulView />);

    fireEvent.click(await screen.findByTestId("soul-file-soul"));
    const editor = (await screen.findByTestId("soul-file-editor")) as HTMLTextAreaElement;
    expect(editor.value).toContain("## Who I am");
    const save = screen.getByTestId("soul-file-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(editor, { target: { value: `${editor.value}\n- **Humor:** dry` } });
    fireEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/soul/file")).toBe(true));
    const put = calls.find((c) => c.url === "/api/soul/file");
    expect(JSON.parse(String(put?.init?.body)).content).toContain("- **Humor:** dry");

    fireEvent.click(screen.getByTestId("soul-back"));
    expect(await screen.findByTestId("soul-hero")).toBeTruthy();
  });

  it("saves the instructions file to the agent-instructions endpoint", async () => {
    const calls = installFetch({
      "GET /api/soul": () => ({ body: profile() }),
      "PUT /api/settings/agent-instructions": () => ({ body: { ok: true } }),
    });
    renderWithClient(<SoulView />);

    fireEvent.click(await screen.findByTestId("soul-file-instructions"));
    const editor = (await screen.findByTestId("soul-file-editor")) as HTMLTextAreaElement;
    expect(editor.value).toBe("Call me Ruben, please.");
    fireEvent.change(editor, { target: { value: "Call me Ruben. Answer in German." } });
    fireEvent.click(screen.getByTestId("soul-file-save"));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/settings/agent-instructions" && c.init?.method === "PUT")).toBe(true),
    );
  });

  it("shows a notebook as its notes, not as an editor", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<SoulView />);

    fireEvent.click(await screen.findByTestId("soul-file-memory"));
    const page = await screen.findByTestId("soul-page-memory");
    expect(within(page).getByText("The sister is called Lena.")).toBeTruthy();
    expect(screen.queryByTestId("soul-file-editor")).toBeNull();
  });
});
