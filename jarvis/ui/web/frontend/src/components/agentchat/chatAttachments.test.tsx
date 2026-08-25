import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentComposer } from "@/components/agentchat/AgentComposer";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { EMPTY_TIMELINE, reduceEvent } from "@/components/agentchat/reduce";
import { useFileDropGuard } from "@/hooks/useFileDropGuard";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import type { AgentChatCatalog } from "@/lib/agentChatApi";

/**
 * Files in the chat composer — the drop, the paste, and what travels with the
 * message.
 *
 * The two chats (the front page and the Agentic IDE's chat mode) share this
 * one composer, so everything asserted here holds for both. What is under test
 * is that a file reaches the backend at all and that its CONTENTS ride along
 * with the sentence: a chat can be answered by a coding CLI or a text-only
 * model, and a bare path leaves those with a filename and a pronoun.
 */

const CATALOG: AgentChatCatalog = {
  default_cwd: "C:\\work",
  shell: "pwsh",
  providers: [
    {
      id: "claude-api",
      label: "Anthropic Claude",
      family: "claude",
      runner: "claude-cli",
      models_source: "curated",
      curated_models: [{ id: "claude-opus-5", label: "Claude Opus 5" }],
      default_model: "",
      keyless: false,
      native_resume: true,
      effort_levels: ["low", "high"],
      default_effort: "high",
      permission_modes: [{ id: "default", label: "Ask before acting", description: "" }],
      default_permission_mode: "default",
      cli_installed: true,
    },
  ],
};

const ATTACHMENT = {
  name: "shot.png",
  reference: '".jarvis/drops/shot.png"',
  kind: "image" as const,
  detail: "A composer with no attach button.",
  described_by: "vision" as const,
  note: "",
};

function seed(overrides: Record<string, unknown> = {}) {
  useAgentChatStore.setState({
    catalog: CATALOG,
    connections: [{ jarvis: "claude-api", key_set: true, is_active_brain: true }],
    catalogError: null,
    backendOutdated: false,
    liveModels: {},
    sessions: [],
    activeSessionId: "s-1",
    activeSession: null,
    timeline: EMPTY_TIMELINE,
    draft: {
      provider: "claude-api",
      model: "",
      effort: "high",
      permissionMode: "default",
      buildMode: "default",
      cwd: "C:\\work",
    },
    busy: false,
    lastError: null,
    loadCatalog: async () => {},
    loadSessions: async () => {},
    loadModels: async () => {},
    ...overrides,
  });
}

function composer() {
  return render(
    <AgentChatStoreProvider store={useAgentChatStore}>
      <AgentComposer />
    </AgentChatStoreProvider>,
  );
}

/** A DataTransfer stand-in: jsdom's own carries neither files nor types. */
function transfer(files: File[]) {
  return {
    files,
    items: files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })),
    types: files.length ? ["Files"] : [],
    getData: () => "",
    dropEffect: "none",
  };
}

describe("chat composer attachments", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    window.localStorage.clear();
    useEventStore.setState({ connected: true, wsWarming: false, assistantName: "Jarvis" });
    seed();
    fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes("/api/agent-chat/attachments")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ attachments: [ATTACHMENT], cwd: "C:\\work" }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({ turn_id: "t-1" }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("takes a pasted image and shows what was read from it", async () => {
    composer();
    const box = document.querySelector("textarea[data-jarvis-chat-input]") as HTMLTextAreaElement;
    const png = new File([new Uint8Array([1, 2, 3])], "image.png", { type: "image/png" });

    await act(async () => {
      fireEvent.paste(box, { clipboardData: transfer([png]) });
    });

    await waitFor(() => expect(screen.getByTestId("chat-attachment-shot.png")).toBeDefined());
    // The chip says the model could SEE it — that is the outcome a person has
    // to be able to read before pressing Send.
    expect(screen.getByTestId("chat-attachment-shot.png").textContent).toContain("described");

    const [url, init] = fetchMock.mock.calls.find(([u]) =>
      String(u).includes("/attachments"),
    ) as [string, RequestInit];
    expect(url).toContain("/api/agent-chat/attachments");
    expect((init.body as FormData).get("session_id")).toBe("s-1");
  });

  it("leaves a pasted TEXT alone so ordinary copy-paste keeps working", async () => {
    composer();
    const box = document.querySelector("textarea[data-jarvis-chat-input]") as HTMLTextAreaElement;

    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", { value: transfer([]) });
    await act(async () => {
      box.dispatchEvent(event);
    });

    // Not claimed, not prevented: the browser inserts the text itself.
    expect(event.defaultPrevented).toBe(false);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/attachments"))).toBe(false);
  });

  it("takes a dropped file and arms the drop overlay while the drag is over", async () => {
    composer();
    const card = screen.getByTestId("agent-composer");
    const png = new File([new Uint8Array([1])], "shot.png", { type: "image/png" });

    fireEvent.dragEnter(card, { dataTransfer: transfer([png]) });
    expect(screen.getByTestId("composer-drop-overlay")).toBeDefined();

    await act(async () => {
      fireEvent.drop(card, { dataTransfer: transfer([png]) });
    });

    await waitFor(() => expect(screen.getByTestId("chat-attachment-shot.png")).toBeDefined());
    expect(screen.queryByTestId("composer-drop-overlay")).toBeNull();
  });

  it("sends the attachments with the sentence and then holds none", async () => {
    const send = vi.fn(async () => {});
    seed({ send });
    composer();
    const box = document.querySelector("textarea[data-jarvis-chat-input]") as HTMLTextAreaElement;
    const png = new File([new Uint8Array([1])], "image.png", { type: "image/png" });

    await act(async () => {
      fireEvent.paste(box, { clipboardData: transfer([png]) });
    });
    await waitFor(() => expect(screen.getByTestId("chat-attachment-shot.png")).toBeDefined());

    fireEvent.change(box, { target: { value: "what is wrong here" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("composer-send"));
    });

    expect(send).toHaveBeenCalledWith("what is wrong here", [ATTACHMENT]);
    // Cleared on send: the next message must not silently re-send the picture.
    expect(screen.queryByTestId("chat-attachment-shot.png")).toBeNull();
  });

  it("lets a picture alone be the whole message", async () => {
    const send = vi.fn(async () => {});
    seed({ send });
    composer();
    const box = document.querySelector("textarea[data-jarvis-chat-input]") as HTMLTextAreaElement;
    const png = new File([new Uint8Array([1])], "image.png", { type: "image/png" });

    await act(async () => {
      fireEvent.paste(box, { clipboardData: transfer([png]) });
    });
    await waitFor(() => expect(screen.getByTestId("chat-attachment-shot.png")).toBeDefined());

    await act(async () => {
      fireEvent.click(screen.getByTestId("composer-send"));
    });
    expect(send).toHaveBeenCalledWith("", [ATTACHMENT]);
  });
});

describe("the timeline's receipt for a message that carried files", () => {
  it("shows the sentence that was typed, not the composed prompt", () => {
    const tl = reduceEvent(EMPTY_TIMELINE, {
      seq: 1,
      ts_ms: 10,
      kind: "user_message",
      payload: {
        text: "what is wrong here\n\n<attachment name=\"shot.png\">…</attachment>",
        typed: "what is wrong here",
        attachments: [{ name: "shot.png", kind: "image", described_by: "vision" }],
      },
    });
    const item = tl.items[0];
    expect(item.type).toBe("user");
    if (item.type !== "user") return;
    expect(item.text).toBe("what is wrong here");
    expect(item.attachments).toEqual([
      { name: "shot.png", kind: "image", describedBy: "vision" },
    ]);
  });

  it("leaves an ordinary message exactly as it was", () => {
    const tl = reduceEvent(EMPTY_TIMELINE, {
      seq: 1,
      ts_ms: 10,
      kind: "user_message",
      payload: { text: "hello" },
    });
    const item = tl.items[0];
    if (item.type !== "user") throw new Error("expected a user item");
    expect(item.text).toBe("hello");
    expect(item.attachments).toEqual([]);
  });
});

describe("the app-wide file drop guard", () => {
  function Guarded() {
    useFileDropGuard();
    return <div data-testid="page">nothing here takes files</div>;
  }

  afterEach(cleanup);

  /** A drop event carrying `types`, with `preventDefault` observable. */
  function dropEvent(types: string[]) {
    const event = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "dataTransfer", {
      value: { ...transfer([]), types },
    });
    const prevent = vi.spyOn(event, "preventDefault");
    return { event, prevent };
  }

  it("swallows a file dropped on chrome that has no drop target", () => {
    render(<Guarded />);
    const { event, prevent } = dropEvent(["Files"]);
    window.dispatchEvent(event);
    // Prevented = the browser will NOT navigate away to the file, which inside
    // the desktop shell would take the whole app with it.
    expect(prevent).toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
  });

  it("keeps its hands off a drop a real target already claimed", () => {
    render(<Guarded />);
    const { event, prevent } = dropEvent(["Files"]);
    event.preventDefault(); // what a drop zone does, below window
    prevent.mockClear();
    window.dispatchEvent(event);
    // The guard saw `defaultPrevented` and stood down — it never touches an
    // event a target is already handling.
    expect(prevent).not.toHaveBeenCalled();
  });

  it("leaves dragged text and links to the browser", () => {
    render(<Guarded />);
    const { event, prevent } = dropEvent(["text/plain"]);
    window.dispatchEvent(event);
    expect(prevent).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });
});
