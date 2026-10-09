import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { readComposerDraft } from "@/components/agentchat/composerDrafts";
import { createAgentChatStore } from "@/store/agentChat";
import type { SocietyAgent } from "../data";
import { Composer } from "./AgentChatPanel";

vi.mock("../data", async (load) => ({
  ...await load<typeof import("../data")>(),
  useSocietyCapabilities: () => ({ data: [], isLoading: false }),
}));
vi.mock("./AgentModelPicker", () => ({ AgentModelPicker: () => null }));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ commands: [] }))));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function write(text: string) {
  const box = screen.getByRole("textbox");
  box.textContent = text;
  fireEvent.input(box);
  return box;
}

function mount(surface: "jarvis" | "society" = "society") {
  const store = createAgentChatStore(surface);
  store.setState({ activeSessionId: "chat-a", busy: true });
  const cancel = vi.fn().mockResolvedValue(undefined);
  const send = vi.fn().mockResolvedValue("sent");
  const tree = () => <AgentChatStoreProvider store={store}>
    <Composer agent={{ agentId: "george", name: "George", tier: surface === "jarvis" ? "lead" : "worker" } as SocietyAgent}
      mentionable={[]} busy sessionId="chat-a" cwd="" provider="local" surface={surface}
      onSend={send} onCancel={cancel} />
  </AgentChatStoreProvider>;
  return { store, send, cancel, tree, view: render(tree()) };
}

it.each(["jarvis", "society"] as const)("keeps only %s Send during startup and accepts rapid followups", async (surface) => {
  const { send, cancel } = mount(surface);
  let accept!: (value: string) => void;
  send.mockImplementationOnce(() => new Promise<string>((resolve) => { accept = resolve; }));
  write("first followup");
  expect(screen.getByTestId("composer-send").hasAttribute("disabled")).toBe(false);
  expect(screen.queryByTestId("composer-stop")).toBeNull();
  fireEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("textbox").textContent).toBe("");
  const box = write("second followup");
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  await act(async () => accept("sent"));
  expect(send.mock.calls.map((args) => args[0])).toEqual(["first followup", "second followup"]);
  expect(cancel).not.toHaveBeenCalled();
});

it("does not send the same draft twice on a rapid repeated submit", async () => {
  const { send } = mount();
  const box = write("once");
  fireEvent.keyDown(box, { key: "Enter" });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
});

it("restores a failed send beside newer typing and keeps that draft across unmounts", async () => {
  const { send, store, view, tree } = mount();
  let fail!: (value: string) => void;
  send.mockImplementationOnce(() => new Promise<string>((resolve) => { fail = resolve; }));
  write("failed message");
  fireEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  write("newer draft");
  view.unmount();
  store.setState({ activeSessionId: "chat-b" });
  await act(async () => fail("failed"));
  expect(readComposerDraft(store, "chat-a").text).toBe("failed message\nnewer draft");
  expect(readComposerDraft(store, "chat-b").text).toBe("");
  store.setState({ activeSessionId: "chat-a" });
  render(tree());
  expect(screen.getByRole("textbox").textContent).toBe("failed message\nnewer draft");
});

it("sends an attachment-only followup while busy and restores the file on HTTP failure", async () => {
  const attachment = { name: "notes.txt", reference: "notes.txt", kind: "text", detail: "notes", described_by: "parser", note: "" };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
    String(url).includes("attachments") ? { attachments: [attachment], cwd: "" } : { commands: [] },
  ))));
  const { send, view } = mount();
  send.mockResolvedValueOnce("failed");
  const input = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
  fireEvent.change(input, { target: { files: [new File(["notes"], "notes.txt", { type: "text/plain" })] } });
  await waitFor(() => expect(screen.getByTestId("composer-send").hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(send).toHaveBeenCalledWith("", [attachment]));
  await waitFor(() => expect(screen.getByTestId("composer-send").hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  expect(send.mock.calls[1]).toEqual(["", [attachment]]);
});

it("keeps newly typed text through session creation and restores a rejected first send into that session", async () => {
  const store = createAgentChatStore("jarvis");
  vi.stubGlobal("WebSocket", undefined);
  let open!: (response: Response) => void;
  let rejectMessage!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/agent-chat/sessions" && init?.method === "POST") {
      return new Promise<Response>((resolve) => { open = resolve; });
    }
    if (url.endsWith("/messages")) {
      return new Promise<Response>((resolve) => { rejectMessage = resolve; });
    }
    return new Response(JSON.stringify({ commands: [], sessions: [] }));
  }));
  function StartingChat() {
    const sid = store((state) => state.activeSessionId);
    const busy = store((state) => state.busy);
    return <AgentChatStoreProvider store={store}>
      <Composer agent={{ agentId: "jarvis", name: "Jarvis", tier: "lead" } as SocietyAgent}
        mentionable={[]} busy={busy} sessionId={sid} cwd="" provider="local"
        onSend={store.getState().send} onCancel={async () => {}} />
    </AgentChatStoreProvider>;
  }
  render(<StartingChat />);
  write("first message");
  fireEvent.click(screen.getByTestId("composer-send"));
  await waitFor(() => expect(open).toBeTypeOf("function"));
  write("second draft");
  await act(async () => open(new Response(JSON.stringify({ session_id: "created", surface: "jarvis" }))));
  await waitFor(() => expect(store.getState().activeSessionId).toBe("created"));
  expect(screen.getByRole("textbox").textContent).toBe("second draft");
  await waitFor(() => expect(rejectMessage).toBeTypeOf("function"));
  await act(async () => rejectMessage(new Response(JSON.stringify({ detail: "Please retry" }), { status: 503 })));
  await waitFor(() => expect(screen.getByRole("textbox").textContent).toBe("first message\nsecond draft"));
  expect(readComposerDraft(store, null).text).toBe("");
  expect(readComposerDraft(store, "created").text).toBe("first message\nsecond draft");
});
