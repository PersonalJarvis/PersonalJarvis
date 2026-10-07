import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("close a field nobody filled when the turn ends", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, asking([ev("turn_finished", { turn_id: "t1", status: "done" })]));
    const block = (tl.items[0] as TurnItem).blocks[0] as ToolBlock;
    expect(block.credential?.status).toBe("cancelled");
  });
});

describe("CredentialCard", () => {
  function draw(events: AgentChatEvent[]) {
    const store = createAgentChatStore("society");
    store.setState({ activeSessionId: "society:ada" });
    const turn = reduceEvents(EMPTY_TIMELINE, events).items[0] as TurnItem;
    render(
      <AgentChatStoreProvider store={store}>
        <ThreadTurn turn={turn} prompts="inline" />
      </AgentChatStoreProvider>,
    );
  }

  it("sends the pasted value once, to the credential route only, and clears the field", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true, request_id: "r1" }), { status: 200 }),
    );
    draw(asking());
    const card = screen.getByTestId("credential-card");
    expect(card.textContent).toContain("GitHub token");
    expect(card.textContent).toContain("To open the pull request.");
    const input = screen.getByLabelText("GitHub token") as HTMLInputElement;
    expect(input.type).toBe("password");
    const save = screen.getByRole("button", { name: "Save securely" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(input, { target: { value: `  ${SECRET}  ` } });
    fireEvent.click(save);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agent-chat/sessions/society%3Aada/credentials/r1");
    expect(JSON.parse(String(init.body))).toEqual({ value: SECRET });
    await waitFor(() => expect(input.value).toBe(""));
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
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    draw(asking());
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agent-chat/sessions/society%3Aada/credentials/r1/decline");
    expect(init.body).toBeUndefined();
  });
});
