import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ThreadTurn } from "@/components/agentic/threads/ThreadTimeline";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { createAgentChatStore } from "@/store/agentChat";
import { AgentChatStoreProvider } from "./AgentChatStoreContext";
import { EMPTY_TIMELINE, reduceEvents, type ToolBlock, type TurnItem } from "./reduce";

let seq = 0;
function ev(kind: string, payload: Record<string, unknown>, tsMs = 1000): AgentChatEvent {
  return { seq: ++seq, ts_ms: tsMs, kind, payload } as AgentChatEvent;
}

const SECRET = "ghp_supersecretvalue123";

function asking(extra: AgentChatEvent[] = []): AgentChatEvent[] {
  return [
    ev("turn_started", { turn_id: "t1" }),
    ev("tool_call", { turn_id: "t1", call_id: "c1", name: "mcp__jarvis__society_request_credential", input: { env: "GITHUB_TOKEN" } }),
    ev("credential_required", {
      turn_id: "t1",
      request_id: "r1",
      asker: "Ada",
      env: "GITHUB_TOKEN",
      label: "GitHub token",
      description: "To open the pull request.",
      placeholder: "",
      replace: false,
      expires_ms: Date.now() + 10 * 60_000,
    }),
    ...extra,
  ];
}

afterEach(() => {
  cleanup();
  seq = 0;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("credential events", () => {
  it("attach the field to the request call and record how it closed", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, asking([
      ev("tool_call", { turn_id: "t1", call_id: "c2", name: "mcp__jarvis__society_request_credential", input: { wait_for: "r1" } }),
      ev("credential_resolved", { turn_id: "t1", request_id: "r1", env: "GITHUB_TOKEN", status: "saved" }),
    ]));
    const turn = tl.items[0] as TurnItem;
    const block = turn.blocks[0] as ToolBlock;
    expect(block.callId).toBe("c1");
    expect(block.credential).toMatchObject({ env: "GITHUB_TOKEN", label: "GitHub token", status: "saved" });
  });

  it.each(["done", "error", "cancelled"])("keeps an unanswered field open when its turn is %s", (status) => {
    const tl = reduceEvents(EMPTY_TIMELINE, asking([
      ev("turn_finished", { turn_id: "t1", status }),
      ev("turn_started", { turn_id: "t2" }),
    ]));
    const block = (tl.items[0] as TurnItem).blocks[0] as ToolBlock;
    expect(block.credential?.status).toBeNull();
  });
});

describe("CredentialCard", () => {
  function draw(events: AgentChatEvent[]) {
    const store = createAgentChatStore("society");
    store.setState({ activeSessionId: "society:ada" });
    const view = (current: AgentChatEvent[]) => (
      <AgentChatStoreProvider store={store}>
        <ThreadTurn turn={reduceEvents(EMPTY_TIMELINE, current).items[0] as TurnItem} prompts="inline" />
      </AgentChatStoreProvider>
    );
    const rendered = render(view(events));
    return { rerender: (current: AgentChatEvent[]) => rendered.rerender(view(current)) };
  }

  it("sends the pasted value once and confirms saving without waiting for the event stream", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true, request_id: "r1", status: "saved" }), { status: 200 }),
    );
    draw(asking());
    const card = screen.getByTestId("credential-card");
    expect(card.textContent).toContain("GitHub token");
    const explanation = screen.getByText("To open the pull request.");
    expect(explanation.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(card.contains(explanation)).toBe(false);
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.getAttribute("aria-describedby")).toBe(explanation.id);
    const save = screen.getByRole("button", { name: "Save securely" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(input, { target: { value: `  ${SECRET}  ` } });
    fireEvent.click(save);
    fireEvent.click(save);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agent-chat/sessions/society%3Aada/credentials/r1");
    expect(JSON.parse(String(init.body))).toEqual({ value: SECRET });
    await waitFor(() => expect(screen.queryByLabelText("GitHub token")).toBeNull());
    expect(screen.getByRole("status").textContent).toContain("GitHub token saved");
    expect(screen.getByTestId("credential-card").textContent).not.toContain(SECRET);
  });

  it("says why a refused value was not stored, in the person's words", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "the credential is empty" }), { status: 400 }),
    );
    draw(asking());
    fireEvent.change(screen.getByLabelText("GitHub token"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    expect((await screen.findByRole("alert")).textContent).toContain("single line");
  });

  it("shows the closed field as one line without any value", () => {
    draw(asking([ev("credential_resolved", { turn_id: "t1", request_id: "r1", env: "GITHUB_TOKEN", status: "saved" })]));
    const card = screen.getByTestId("credential-card");
    expect(card.getAttribute("data-state")).toBe("saved");
    expect(card.textContent).toBe("GitHub token saved · available as GITHUB_TOKEN");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("declines without sending anything secret", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true, request_id: "r1", status: "declined" }), { status: 200 }),
    );
    draw(asking());
    fireEvent.change(screen.getByLabelText("GitHub token"), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agent-chat/sessions/society%3Aada/credentials/r1/decline");
    expect(init.body).toBeUndefined();
    await waitFor(() => expect(screen.getByTestId("credential-card").getAttribute("data-state")).toBe("declined"));
  });

  it("retains a partially pasted value when the turn finishes and its work folds", () => {
    const events = asking([
      ev("tool_result", { turn_id: "t1", call_id: "c1", output: "Waiting for the person" }),
      ev("tool_call", { turn_id: "t1", call_id: "c2", name: "Read", input: { file: "README.md" } }),
      ev("tool_result", { turn_id: "t1", call_id: "c2", output: "Read complete" }),
      ev("text_delta", { turn_id: "t1", text: "Enter the token when you are ready." }),
    ]);
    const { rerender } = draw(events);
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    fireEvent.change(input, { target: { value: SECRET } });
    rerender([...events, ev("turn_finished", { turn_id: "t1", status: "done" })]);
    expect(screen.getByLabelText("GitHub token")).toBe(input);
    expect(input.value).toBe(SECRET);
    fireEvent.click(screen.getByTestId("thread-worked-for"));
    fireEvent.click(screen.getByTestId("thread-worked-for"));
    expect(input.value).toBe(SECRET);
  });

  it("stays open across elapsed time, blur, Escape and outside clicks", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    draw(asking());
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    fireEvent.change(input, { target: { value: SECRET } });
    fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.click(document.body);
    await act(async () => { await vi.advanceTimersByTimeAsync(20 * 60_000); });
    expect(screen.getByTestId("credential-card").getAttribute("data-state")).toBe("open");
    expect(input.value).toBe(SECRET);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["invalid_token", 422, "service rejected"],
    ["network_error", 503, "reach the service"],
    ["validation_timeout", 504, "timed out"],
    ["storage_failed", 503, "saved securely"],
    ["request_busy", 409, "already being checked"],
  ])("keeps %s failures retryable without rendering response secrets", async (code, status, message) => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code, message: SECRET } }), { status: Number(status) }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, request_id: "r1", status: "saved" }), { status: 200 }));
    draw(asking());
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    fireEvent.change(input, { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(screen.getByTestId("credential-card").textContent).not.toContain(SECRET);
    expect(input.value).toBe(SECRET);
    expect(input.disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    await waitFor(() => expect(screen.getByTestId("credential-card").getAttribute("data-state")).toBe("saved"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps transport errors visible and retryable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    draw(asking());
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    fireEvent.change(input, { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Check your connection");
    expect(input.disabled).toBe(false);
    expect(input.value).toBe(SECRET);
  });

  it("bounds a hanging submission without expiring the input", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    draw(asking());
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    fireEvent.change(input, { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByRole("alert").textContent).toContain("timed out");
    expect(input.disabled).toBe(false);
    expect(input.value).toBe(SECRET);
  });

  it("does not claim a save when the response is unconfirmed", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    draw(asking());
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    fireEvent.change(input, { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    await screen.findByRole("alert");
    expect(input.value).toBe(SECRET);
    expect(input.disabled).toBe(false);
  });
});
