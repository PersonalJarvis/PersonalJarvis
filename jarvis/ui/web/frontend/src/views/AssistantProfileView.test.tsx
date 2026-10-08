/**
 * Component tests for the assistant's profile section.
 *
 * What these pin: the page carries the assistant's name (never a file name),
 * its vibe leads the page, the character reads from SOUL.md without repeating
 * the vibe, explicit memories say so, a note can be forgotten only while the
 * learning loop runs, plural counts are right, and the files open in place —
 * SOUL.md and the instructions as editors that save to their own endpoints.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AssistantProfileView } from "@/views/AssistantProfileView";
import type { SoulProfile } from "@/views/assistant/api";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function profile(overrides: Partial<SoulProfile> = {}): SoulProfile {
  return {
    name: "Nova",
    named: true,
    product: "Personal Jarvis",
    wake_phrase: "Hey Nova",
    learning: true,
    files: [
      {
        id: "soul",
        filename: "SOUL.md",
        exists: true,
        editable: true,
        updated_ms: Date.now(),
        chars: 120,
        content: "# My own persona\n\n## Who I am\n\n- **Name:** Nova\n- **Vibe:** Dry and direct.\n",
        character: {
          who: [
            { label: "Role", text: "Personal voice assistant." },
            { label: "Vibe", text: "Dry and direct." },
          ],
          tone: [{ label: "", text: "No corporate-speak." }],
          limits: [{ label: "", text: "No made-up facts." }],
        },
        learned: [{ id: "s1", text: "Answers in short spoken sentences.", importance: 5, origin: "review", explicit: false }],
        name_in_file: "Nova",
      },
      {
        id: "instructions",
        filename: "Nova.md",
        exists: true,
        editable: true,
        updated_ms: Date.now(),
        chars: 21,
        content: "Call me Ruben, please.",
        template: "# How Nova should work with me\n",
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
    return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AssistantProfileView", () => {
  it("is titled with the assistant's name and leads with its vibe", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<AssistantProfileView />);

    expect(await screen.findByTestId("assistant-intro")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Nova");
    expect(screen.getByText(/woken by “Hey Nova”/)).toBeTruthy();
    expect(screen.getByTestId("assistant-vibe").textContent).toBe("Dry and direct.");
    expect(screen.getByText("1 thing remembered")).toBeTruthy();
    expect(screen.getByTestId("assistant-learning").textContent).toBe("Learning");
  });

  it("shows the character without repeating the vibe", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<AssistantProfileView />);

    const character = await screen.findByTestId("assistant-character");
    expect(within(character).getByText("Personal voice assistant.")).toBeTruthy();
    expect(within(character).getByText("No corporate-speak.")).toBeTruthy();
    expect(within(character).queryByText("Dry and direct.")).toBeNull();
  });

  it("lists memories with who asked, and the ledger with the person's words", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<AssistantProfileView />);

    const memory = await screen.findByTestId("assistant-memory");
    expect(within(memory).getByText("The sister is called Lena.")).toBeTruthy();
    expect(within(memory).getByText(/You asked/)).toBeTruthy();
    fireEvent.click(within(memory).getByRole("button", { name: /About itself/ }));
    expect(within(memory).getByText("Answers in short spoken sentences.")).toBeTruthy();

    const recent = screen.getByTestId("assistant-recent");
    expect(within(recent).getByText("Added to memory")).toBeTruthy();
    expect(within(recent).getByText("You said: “merk dir das”")).toBeTruthy();
  });

  it("shows each file with its own first lines and the right count", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<AssistantProfileView />);

    const soul = await screen.findByTestId("assistant-file-soul");
    expect(within(soul).getByText("Name: Nova")).toBeTruthy();
    expect(within(soul).queryByText(/My own persona/)).toBeNull();
    expect(within(screen.getByTestId("assistant-file-instructions")).getByText("Call me Ruben, please.")).toBeTruthy();
    const memory = screen.getByTestId("assistant-file-memory");
    expect(within(memory).getByText(/^1 note ·/)).toBeTruthy();
    expect(within(memory).getByText("The sister is called Lena.")).toBeTruthy();
    expect(within(screen.getByTestId("assistant-file-user")).getByText("Not created yet")).toBeTruthy();
  });

  it("lets the person choose how much initiative it takes", async () => {
    const initiative = {
      level: "balanced" as const,
      levels: ["off", "balanced", "high"] as ("off" | "balanced" | "high")[],
      upcoming: [{ date: "2026-10-09", text: "The user is preparing a product launch for 2026-10-09." }],
    };
    const calls = installFetch({
      "GET /api/soul": () => ({ body: profile({ initiative }) }),
      "PUT /api/soul/initiative": () => ({
        body: profile({ initiative: { ...initiative, level: "off", upcoming: [] } }),
      }),
    });
    renderWithClient(<AssistantProfileView />);

    const section = await screen.findByTestId("assistant-initiative");
    const radios = within(section).getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
    expect(within(section).getByText(/nothing is sent, bought, deleted or started without your yes/)).toBeTruthy();
    expect(within(screen.getByTestId("assistant-initiative-upcoming")).getByText(/product launch/)).toBeTruthy();

    fireEvent.click(screen.getByTestId("assistant-initiative-off"));
    await waitFor(() =>
      expect(screen.getByTestId("assistant-initiative-off").getAttribute("aria-checked")).toBe("true"),
    );
    const put = calls.find((c) => c.url === "/api/soul/initiative");
    expect(put?.init?.method).toBe("PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ level: "off" });
    // Off: nothing is pointed at, so no dated plans are listed.
    await waitFor(() => expect(screen.queryByTestId("assistant-initiative-upcoming")).toBeNull());
  });

  it("hides the initiative section for a backend without the setting", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile() }) });
    renderWithClient(<AssistantProfileView />);

    await screen.findByTestId("assistant-character");
    expect(screen.queryByTestId("assistant-initiative")).toBeNull();
  });

  it("forgets a note through the soul endpoint", async () => {
    const calls = installFetch({
      "GET /api/soul": () => ({ body: profile() }),
      "DELETE /api/soul/entries/memory/m1": () => ({
        body: profile({ files: profile().files.map((f) => (f.id === "memory" ? { ...f, entries: [] } : f)) }),
      }),
    });
    renderWithClient(<AssistantProfileView />);

    const memory = await screen.findByTestId("assistant-memory");
    fireEvent.click(within(memory).getByRole("button", { name: /Forget: The sister/ }));
    await waitFor(() => expect(within(memory).getByTestId("assistant-notes-empty-memory")).toBeTruthy());
    expect(calls.some((c) => c.url === "/api/soul/entries/memory/m1" && c.init?.method === "DELETE")).toBe(true);
  });

  it("cannot forget while the learning loop is off", async () => {
    installFetch({ "GET /api/soul": () => ({ body: profile({ learning: false }) }) });
    renderWithClient(<AssistantProfileView />);

    const memory = await screen.findByTestId("assistant-memory");
    const forget = within(memory).getByRole("button", { name: /Forget: The sister/ }) as HTMLButtonElement;
    expect(forget.disabled).toBe(true);
    expect(screen.getByTestId("assistant-learning").textContent).toBe("Not learning right now");
  });

  it("edits SOUL.md in place and saves it to /api/soul/file", async () => {
    const calls = installFetch({
      "GET /api/soul": () => ({ body: profile() }),
      "PUT /api/soul/file": () => ({ body: profile() }),
    });
    renderWithClient(<AssistantProfileView />);

    const character = await screen.findByTestId("assistant-character");
    fireEvent.click(within(character).getByRole("button", { name: "Edit" }));
    const editor = (await screen.findByTestId("assistant-file-editor")) as HTMLTextAreaElement;
    expect(editor.value).toContain("## Who I am");
    const save = screen.getByTestId("assistant-file-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(editor, { target: { value: `${editor.value}\n- **Humor:** dry` } });
    fireEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/soul/file")).toBe(true));
    const put = calls.find((c) => c.url === "/api/soul/file");
    expect(JSON.parse(String(put?.init?.body)).content).toContain("- **Humor:** dry");

    fireEvent.click(screen.getByTestId("assistant-back"));
    expect(await screen.findByTestId("assistant-intro")).toBeTruthy();
  });

  it("saves the instructions file to the agent-instructions endpoint", async () => {
    const calls = installFetch({
      "GET /api/soul": () => ({ body: profile() }),
      "PUT /api/settings/agent-instructions": () => ({ body: { ok: true } }),
    });
    renderWithClient(<AssistantProfileView />);

    fireEvent.click(await screen.findByTestId("assistant-file-instructions"));
    const editor = (await screen.findByTestId("assistant-file-editor")) as HTMLTextAreaElement;
    expect(editor.value).toBe("Call me Ruben, please.");
    fireEvent.change(editor, { target: { value: "Call me Ruben. Answer in German." } });
    fireEvent.click(screen.getByTestId("assistant-file-save"));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/settings/agent-instructions" && c.init?.method === "PUT")).toBe(true),
    );
  });
});
