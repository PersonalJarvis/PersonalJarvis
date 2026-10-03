import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SessionsView } from "./SessionsView";
import { transcriptFixture } from "@/components/transcription/testFixtures";
import { useEventStore } from "@/store/events";
import { useI18nStore } from "@/i18n";

let detail = transcriptFixture();
let listStatus = 200;
let detailStatus = 200;
let empty = false;
let loading = false;
let exportStatus = 200;
let client: QueryClient;
let requests: string[];
const copy = vi.fn(async (_text: string) => undefined);

beforeEach(() => {
  detail = transcriptFixture();
  listStatus = detailStatus = exportStatus = 200;
  empty = loading = false;
  requests = [];
  copy.mockClear();
  useI18nStore.setState({ ui: "en" });
  useEventStore.setState({ assistantName: "Assistant", events: [], activeSection: "sessions" });
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = String(input);
    requests.push(url);
    if (url.startsWith("/api/sessions?")) {
      if (loading) return new Promise<Response>(() => {});
      return new Response(JSON.stringify(empty ? [] : [{ ...detail.session, duration_s: 30, preview: "Find my meeting notes" }]), { status: listStatus });
    }
    if (url.includes("/export?")) return new Response("Exported conversation", { status: exportStatus });
    if (url.startsWith("/api/sessions/")) return new Response(JSON.stringify(detail), { status: detailStatus });
    if (url === "/api/downloads/capabilities") return Response.json({ native_file_actions: false, platform: "linux" });
    throw new Error(`Unexpected request: ${url}`);
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
});

afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); });

function mount() { return render(<QueryClientProvider client={client}><SessionsView /></QueryClientProvider>); }
async function openConversation() { fireEvent.click(await screen.findByRole("button", { name: /Find my meeting notes/ })); }

describe("Transcription workspace", () => {
  it("opens a reading page and restores the filtered archive and keyboard focus on return", async () => {
    mount();
    const row = await screen.findByRole("button", { name: /Find my meeting notes/ });
    expect(requests.some((url) => url.includes("/api/sessions/"))).toBe(false);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "meeting" } });
    fireEvent.click(row);
    expect(await screen.findByText("Here are the [notes].")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Find my meeting notes" })).toBe(document.activeElement);
    expect(screen.queryByText("An unspoken draft")).toBeNull();
    expect(screen.queryByText("private-model")).toBeNull();
    expect(screen.getByText("Ready to listen.")).toBeTruthy();
    expect(screen.getByText("Your background task is complete.")).toBeTruthy();
    expect(screen.getByText("Awaiting confirmation")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "All conversations" }));
    await waitFor(() => expect(document.activeElement).toBe(row));
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("meeting");
  });

  it("searches literal punctuation, switches original wording and copies only the selected excerpt", async () => {
    mount(); await openConversation();
    await screen.findByText("Here are the [notes].");
    fireEvent.click(screen.getByRole("checkbox", { name: "Original wording" }));
    expect(screen.getByText("um find [notes] please")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Find in this transcript" }), { target: { value: "[notes]" } });
    expect(document.querySelectorAll("mark")).toHaveLength(2);
    expect(screen.queryByText("Ready to listen.")).toBeNull();
    const excerpt = screen.getByRole("article");
    fireEvent.click(within(excerpt).getByRole("button", { name: /copy/i }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith(expect.stringContaining("um find [notes] please")));
    expect(copy.mock.calls[0][0]).not.toContain("private-model");
    fireEvent.change(screen.getByRole("searchbox", { name: "Find in this transcript" }), { target: { value: "no such phrase" } });
    expect(screen.getByText("No matching words")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Clear search" }).at(-1)!);
    expect(screen.getByText("Ready to listen.")).toBeTruthy();
  });

  it("exports through the existing encoded endpoint and reports failures", async () => {
    mount(); await openConversation();
    fireEvent.click(await screen.findByRole("button", { name: "Copy transcript" }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith("Exported conversation"));
    expect(requests).toContain("/api/sessions/voice%2Fone/export?format=plain");
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    fireEvent.change(screen.getByRole("combobox", { name: "File format" }), { target: { value: "json" } });
    expect(screen.getByRole("link", { name: "Open file" }).getAttribute("href")).toBe("/api/sessions/voice%2Fone/export?format=json");
    exportStatus = 500;
    const panel = screen.getByRole("combobox").parentElement!;
    fireEvent.click(within(panel).getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/clipboard/i));
  });

  it("has distinct loading and empty states", async () => {
    loading = true; mount();
    expect(screen.getByText("Loading your conversations…")).toBeTruthy();
    expect(screen.queryByText("Your conversations belong here")).toBeNull();
    loading = false; empty = true;
    await act(async () => { client.setQueryData(["sessions"], []); });
    fireEvent.click(await screen.findByRole("button", { name: "Go to chat" }));
    expect(useEventStore.getState().activeSection).toBe("chats");
  });

  it("explains a disabled recorder without presenting a false empty archive", async () => {
    listStatus = 503; mount();
    expect(await screen.findByText("Transcription is unavailable")).toBeTruthy();
    expect(screen.queryByText("Your conversations belong here")).toBeNull();
    listStatus = 200;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: /Find my meeting notes/ })).toBeTruthy();
  });

  it("offers recovery and a way back when a transcript cannot load", async () => {
    detailStatus = 500; mount(); await openConversation();
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "All conversations" })).toBeTruthy();
    detailStatus = 200;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Here are the [notes].")).toBeTruthy();
  });
});
