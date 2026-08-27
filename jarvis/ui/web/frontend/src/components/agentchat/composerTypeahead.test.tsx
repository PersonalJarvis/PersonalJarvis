import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentComposer } from "@/components/agentchat/AgentComposer";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { EMPTY_TIMELINE } from "@/components/agentchat/reduce";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import type { AgentChatCatalog } from "@/lib/agentChatApi";

/**
 * The list over the composer after "/", "@" or "$" — the gesture every
 * coding CLI has, on the chat's own text box.
 *
 * What is under test: the list opens only for the triggers the catalog row
 * names, it shows what the backend read, the arrows and Enter pick without
 * sending, Escape dismisses for that token, and a seat with no triggers gets
 * no list at all.
 */

function catalog(typeahead?: string[]): AgentChatCatalog {
  return {
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
        ...(typeahead ? { typeahead } : {}),
      },
    ],
  };
}

function seed(cat: AgentChatCatalog, overrides: Record<string, unknown> = {}) {
  useAgentChatStore.setState({
    catalog: cat,
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

function box(): HTMLTextAreaElement {
  return document.querySelector("textarea[data-jarvis-chat-input]") as HTMLTextAreaElement;
}

/** Type into the box the way a person does: the value lands, the caret sits at its end. */
function type(text: string) {
  const el = box();
  fireEvent.change(el, { target: { value: text } });
  el.setSelectionRange(text.length, text.length);
  fireEvent.select(el);
}

const SLASH_ITEMS = [
  { value: "commit", label: "commit", hint: "Commit the work", kind: "skill", group: "project" },
  { value: "review", label: "review", hint: "Review the diff", kind: "command", group: "project" },
  { value: "github:issue", label: "github:issue", hint: "Open an issue", kind: "skill", group: "plugins" },
];
const FILE_ITEMS = [
  { value: "src/", label: "src/", hint: "", kind: "folder", group: "files" },
  { value: "src/app.py", label: "app.py", hint: "src", kind: "file", group: "files" },
];

describe("composer typeahead", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let sent: string[];

  beforeEach(() => {
    window.localStorage.clear();
    useEventStore.setState({ connected: true, wsWarming: false, assistantName: "Jarvis" });
    sent = [];
    fetchMock = vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/api/agent-chat/typeahead")) {
        const params = new URL(u, "http://x").searchParams;
        const trigger = params.get("trigger");
        return {
          ok: true,
          status: 200,
          json: async () => ({
            trigger,
            items: trigger === "/" ? SLASH_ITEMS : FILE_ITEMS,
            truncated: false,
          }),
        } as Response;
      }
      if (u.includes("/messages")) sent.push(u);
      return { ok: true, status: 200, json: async () => ({ turn_id: "t-1" }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("opens the slash list with what the backend read, grouped, and picks with Enter without sending", async () => {
    seed(catalog(["/", "@"]));
    composer();
    type("/");

    await waitFor(() => expect(screen.getByTestId("composer-typeahead")).toBeTruthy());
    const rows = screen.getAllByTestId("composer-typeahead-item");
    expect(rows.map((r) => r.textContent)).toEqual([
      "/commitCommit the work",
      "/reviewReview the diff",
      "/github:issueOpen an issue",
    ]);
    expect(screen.getByTestId("composer-typeahead").textContent).toContain("Plugins");
    const asked = new URL(String(fetchMock.mock.calls[0][0]), "http://x").searchParams;
    // The default store speaks for the front page; the IDE's store would say "agent".
    expect(asked.get("surface")).toBe("jarvis");
    expect(asked.get("provider")).toBe("claude-api");
    expect(asked.get("cwd")).toBe("C:\\work");

    // Narrow, then arrow down to the second match and take it.
    type("/re");
    await waitFor(() =>
      expect(screen.getAllByTestId("composer-typeahead-item").map((r) => r.getAttribute("data-index"))).toEqual([
        "0",
      ]),
    );
    expect(screen.getByTestId("composer-typeahead-item").textContent).toContain("/review");
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(box().value).toBe("/review "));
    expect(sent).toEqual([]);
    expect(screen.queryByTestId("composer-typeahead")).toBeNull();
  });

  it("asks the backend per keystroke for @ and keeps a folder pick open on its contents", async () => {
    seed(catalog(["/", "@"]));
    composer();
    type("look at @sr");

    // The list opens at once ("Looking…"); the request follows the debounce.
    await waitFor(() => expect(screen.getByTestId("composer-typeahead")).toBeTruthy());
    const asked = () =>
      fetchMock.mock.calls
        .map((c) => new URL(String(c[0]), "http://x").searchParams)
        .filter((p) => p.get("trigger") === "@");
    await waitFor(() => expect(asked().at(-1)?.get("q")).toBe("sr"));
    await waitFor(() => expect(screen.getAllByTestId("composer-typeahead-item")).toHaveLength(2));

    fireEvent.keyDown(box(), { key: "ArrowDown" });
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    fireEvent.keyDown(box(), { key: "Tab" });
    await waitFor(() => expect(box().value).toBe("look at @src/"));
    expect(sent).toEqual([]);
  });

  it("dismisses on Escape for that token only, and Enter then sends", async () => {
    seed(catalog(["/", "@"]));
    composer();
    type("/");
    await waitFor(() => expect(screen.getByTestId("composer-typeahead")).toBeTruthy());

    fireEvent.keyDown(box(), { key: "Escape" });
    expect(screen.queryByTestId("composer-typeahead")).toBeNull();
    type("/c");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.queryByTestId("composer-typeahead")).toBeNull();

    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(sent.length).toBe(1));
  });

  it("gives a seat with no triggers no list at all", async () => {
    seed(catalog());
    composer();
    type("/");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.queryByTestId("composer-typeahead")).toBeNull();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/typeahead"))).toBe(false);
  });
});
