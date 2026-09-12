import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useRef, useState } from "react";
import { CHAT_COMMAND_NAMES, parseChatCommand, type ChatControlState } from "@/lib/chatControlApi";
import { createAgentChatStore } from "@/store/agentChat";
import { EMPTY_TIMELINE, reduceEvent } from "./reduce";
import { AgentChatStoreProvider } from "./AgentChatStoreContext";
import { ChatCommandPanel, useChatCommands } from "./ChatCommands";
import { useTranscriptViewStore } from "../society/chat/useTranscriptView";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("../society/card/AgentRoutinesList", () => ({ AgentRoutinesList: ({ agentId }: { agentId: string }) => <div>Routines: {agentId}</div> }));
const store = createAgentChatStore("society");
const state: ChatControlState = { session_id: "society:test", mode: "build", permission_mode: "ask",
  previous_permission: "ask", output_language: "en", last_request: "Check tickets", last_status: "done", plan: "", goal: null, revision: 1 };
const model = vi.fn();
let requests: { command: string; request_id: string; arguments: string }[] = [];
let failOnce = false;

function Harness() {
  const [value, setValue] = useState("");
  const anchorRef = useRef<HTMLDivElement>(null);
  const commands = useChatCommands({ value, setValue, onModel: model, agentId: "test" });
  return <div role="dialog"><div style={{ overflow: "hidden" }} data-testid="scroll-container"><div ref={anchorRef}>
    <ChatCommandPanel control={commands} anchorRef={anchorRef} /><input aria-label="Message" value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={commands.onKeyDown} />
    <button onClick={() => void commands.execute(value)}>Send</button></div></div></div>;
}
function type(text: string) { fireEvent.change(screen.getByRole("textbox"), { target: { value: text } }); }
function send(text: string) { type(text); fireEvent.click(screen.getByText("Send")); }
beforeEach(() => {
  requests = []; failOnce = false; model.mockClear();
  useTranscriptViewStore.setState({ boundaries: {} });
  store.setState({ activeSessionId: state.session_id, timeline: { ...EMPTY_TIMELINE, items: [
    { type: "user", id: "u-1", text: "Keep this context", attachments: [], tsMs: 1 },
  ] }, busy: false });
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    if (!init?.method) return new Response(JSON.stringify({ state, commands: CHAT_COMMAND_NAMES.map((name) => ({ name, available: true, kind: "control", example: `/${name}` })) }));
    const body = JSON.parse(init.body);
    requests.push(body);
    if (failOnce) { failOnce = false; throw new Error("Connection lost"); }
    return new Response(JSON.stringify({ request_id: body.request_id, command: body.command, status: "done", error: "", state: { ...state, mode: body.command === "plan" ? "plan" : "build", permission_mode: body.command === "plan" ? "plan" : "ask", revision: 2 }, data: { hits: [] } }));
  }));
  render(<AgentChatStoreProvider store={store}><Harness /></AgentChatStoreProvider>);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("offers all sixteen commands and supports keyboard selection", async () => {
  type("/");
  await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(16));
  type("/go");
  expect(screen.getAllByRole("option")).toHaveLength(1);
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Tab" });
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("/goal ");
  expect(requests).toHaveLength(0);
});

it("floats outside the clipping container while staying inside the agent dialog", async () => {
  type("/");
  const popup = await screen.findByRole("listbox");
  expect(popup.parentElement).toBe(screen.getByRole("dialog"));
  expect(screen.getByTestId("scroll-container").contains(popup)).toBe(false);
  expect(popup.classList.contains("fixed")).toBe(true);
  expect(popup.style.bottom).not.toBe("");
});

it("selects an exact command on Enter and dismisses without losing the draft", async () => {
  type("/clear");
  await screen.findByRole("option");
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("/clear ");
  expect(requests).toHaveLength(0);
  type("/go");
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("/go");
  type("/goal");
  expect(await screen.findByRole("listbox")).toBeTruthy();
});

it("clears and restores only the local view", async () => {
  const original = store.getState().timeline;
  send("/clear");
  await waitFor(() => expect(useTranscriptViewStore.getState().boundaries[state.session_id]).toBeTruthy());
  send("/history");
  await waitFor(() => expect(useTranscriptViewStore.getState().boundaries[state.session_id]).toBeUndefined());
  expect(store.getState().timeline).toBe(original);
  expect(requests).toHaveLength(0);
});

it("runs actual mode changes and keeps a request id across network retries", async () => {
  failOnce = true;
  send("/plan Inspect tickets");
  await screen.findByRole("alert");
  send("/plan Inspect tickets");
  await screen.findByText("slash.plan_active");
  expect(requests).toHaveLength(2);
  expect(requests[0].request_id).toBe(requests[1].request_id);
  expect(requests[1].arguments).toBe("Inspect tickets");
  expect(store.getState().draft.permissionMode).toBe("plan");
});

it("opens existing model and routine controls without sending an agent prompt", async () => {
  send("/model");
  await waitFor(() => expect(model).toHaveBeenCalledOnce());
  send("/routines");
  expect(await screen.findByText("Routines: test")).toBeTruthy();
  expect(requests).toHaveLength(0);
});

it("updates a running goal from the stream and accepts stop while the chat is busy", async () => {
  await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
  act(() => store.setState({ busy: true, timeline: reduceEvent(store.getState().timeline, {
    seq: 2, ts_ms: 2, kind: "notice", payload: { kind: "chat_control", state: { ...state, revision: 5,
      goal: { id: "g", objective: "Finish tickets", status: "active", reason: "", engine: "jarvis", steps: 1, stalled_steps: 0, evidence: [], started_ms: 1, updated_ms: 2 } } },
  }) }));
  expect(await screen.findByTestId("chat-goal")).toBeTruthy();
  send("/stop");
  await waitFor(() => expect(requests.at(-1)?.command).toBe("stop"));
  expect(store.getState().timeline.items).toHaveLength(1);
});

it("never interprets quoted commands, attachments, paths or embedded text as actions", () => {
  for (const text of ["Please use /goal later", "```\n/stop\n```", "/clearance", "/tmp/file.txt", "//stop"]) expect(parseChatCommand(text)).toBeNull();
  expect(parseChatCommand(" /message @Agent Hello ")).toEqual({ name: "message", args: "@Agent Hello" });
});
