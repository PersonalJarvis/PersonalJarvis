import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MeetingChat } from "./MeetingChat";
import type { SocietyAgent } from "../data";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const group = { group_id: "team", name: "Team", members: ["jarvis", "scout"], created_ms: 1, updated_ms: 1 };
const roster = [{ agentId: "jarvis", name: "Jarvis" }, { agentId: "scout", name: "Scout" }] as SocietyAgent[];
const empty = { messages: [], running: false, room: null };
function mount(members = group.members) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MeetingChat group={{ ...group, members }} roster={roster} /></QueryClientProvider>);
  return client;
}

it("reads without starting turns, then sends exactly one explicit request and stops the round", async () => {
  let data: any = empty;
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      data = _url.endsWith("/stop") ? { ...data, running: false } : {
        messages: [{ id: "1", speaker: "user", text: "Compare ideas" }, { id: "2", speaker: "scout", text: "My idea" }],
        running: true, room: { state: "running", next_speaker: "jarvis", settle_reason: "" },
      };
    }
    return { ok: true, json: async () => data };
  });
  vi.stubGlobal("fetch", fetcher);
  mount();
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  expect(fetcher.mock.calls[0][1]).toBeUndefined();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Compare ideas" } });
  fireEvent.click(screen.getByRole("button", { name: "society.meeting.send" }));
  await screen.findByText("My idea");
  expect(screen.getByText("Scout")).toBeTruthy();
  expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).toEqual({ text: "Compare ideas" });
  expect((screen.getByRole("button", { name: "society.meeting.send" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "society.meeting.stop" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(screen.queryByRole("button", { name: "society.meeting.stop" })).toBeNull());
});

it("keeps an unsent message when the request fails and blocks oversized meetings", async () => {
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => ({
    ok: !init, json: async () => init ? { detail: "Agent is busy" } : empty,
  }));
  vi.stubGlobal("fetch", fetcher);
  mount();
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Keep this" } });
  fireEvent.click(screen.getByRole("button", { name: "society.meeting.send" }));
  await screen.findByRole("alert");
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Keep this");
  cleanup();
  mount(["a", "b", "c", "d", "e", "f", "g"]);
  expect(screen.getByText("society.meeting.limit")).toBeTruthy();
  expect((screen.getByRole("button", { name: "society.meeting.send" }) as HTMLButtonElement).disabled).toBe(true);
});

it("does not poll an idle meeting", async () => {
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => empty }));
  vi.stubGlobal("fetch", fetcher);
  mount();
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await new Promise((resolve) => setTimeout(resolve, 2_300));
  expect(fetcher).toHaveBeenCalledOnce();
});
