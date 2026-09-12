import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  Sidebar,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_RAIL_AT_WIDTH,
  SIDEBAR_RAIL_WIDTH,
} from "@/components/layout/Sidebar";
import { NAV_GROUPS, NAV_FOOTER_ITEMS } from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useIdeChatStore } from "@/store/ideChat";

// The sidebar header avatar must mirror the chosen on-screen display style:
// the ghost mascot ONLY when the user explicitly picked "mascot"; the slim bar
// for "jarvis_bar"/"none" and while the style is still loading (config null).
// Mock the overlay-style hook so the test controls the style without a fetch.
const overlayMock = vi.hoisted(() => ({ style: "jarvis_bar" as string | null }));
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({
    config: overlayMock.style
      ? { style: overlayMock.style, options: ["jarvis_bar", "mascot", "none"] }
      : null,
    loading: false,
    error: null,
    refetch: () => {},
    saveStyle: () => {},
  }),
}));

// usePluginAttention polls /api/marketplace/plugins; mock it so the sidebar's
// plugin reconnect dot is driven by the test, not a fetch.
const pluginAttentionMock = vi.hoisted(() => ({ needsReconnect: false }));
vi.mock("@/hooks/usePluginAttention", () => ({
  usePluginAttention: () =>
    pluginAttentionMock.needsReconnect
      ? { count: 1, names: ["Cloudflare"] }
      : { count: 0, names: [] },
}));

// useVoiceMode fetches /api/settings/voice-mode; mock it so the footer card's
// pipeline-vs-realtime split is driven by the test, not a fetch. The default
// mirrors a fresh pipeline install (the pre-existing footer tests rely on it).
const voiceModeMock = vi.hoisted(() => ({
  value: {
    mode: "pipeline",
    activeProvider: null as string | null,
    activeProviderLabel: null as string | null,
    activeModel: null as string | null,
    sessionActive: false,
    activeSessionMode: null as "pipeline" | "realtime" | null,
    activeSessionProvider: "",
    activeSessionModel: "",
  },
}));
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => voiceModeMock.value,
}));

function resetVoiceModeMock() {
  voiceModeMock.value = {
    mode: "pipeline",
    activeProvider: null,
    activeProviderLabel: null,
    activeModel: null,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
  };
}

function renderSidebar(width?: number) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <Sidebar width={width} />
    </QueryClientProvider>,
  );
}

describe("Sidebar voice header", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("does not render the floating mascot bubble while listening", () => {
    // The mascot's listening speech-bubble is anchored to the left of the
    // mascot (right: calc(100% + 10px)). In the sidebar the mascot sits flush
    // against the window edge, so the bubble slides off-screen and only its
    // yellow border + glow bleed back in — the spurious "yellow frame" the
    // user reported. The sidebar must not render that bubble.
    useEventStore.setState({
      voiceState: "listening",
      transcription: "auflegen",
      transcriptionFinal: false,
    });

    const { container } = renderSidebar();

    expect(container.querySelector(".gigi-bubble-listening")).toBeNull();
    expect(container.querySelector(".gigi-bubble")).toBeNull();
  });

  test("no longer shows the live transcription — that lane moved to the voice stage", () => {
    // Since 2026-08-23 the transcript is read on the front page itself
    // (components/home/VoiceStage), where it has the room to be read; the
    // sidebar box would be a second, cramped copy of the same words.
    useEventStore.setState({
      voiceState: "listening",
      transcription: "auflegen",
      transcriptionFinal: false,
    });

    renderSidebar();

    expect(screen.queryByText("auflegen")).toBeNull();
  });

  test("carries the Voice | Chat switch and a New chat button", () => {
    renderSidebar();
    expect(screen.getByTestId("home-surface-switch")).toBeTruthy();
    expect(screen.getByTestId("sidebar-new-chat")).toBeTruthy();
  });
});

/*
 * "+ New" starts a conversation of the KIND on screen. Standing on the voice
 * stage and being thrown onto the chat page was the reported bug: the button
 * looked like it did nothing you asked for.
 */
describe("Sidebar new-conversation button", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      // GET /api/chats answers with a list, the agent-chat sessions probe with
      // an empty roster; every other call the mounted sidebar fires gets a
      // harmless object. An undefined list here crashes the recent-chats block.
      vi.fn(async (url: string) =>
        String(url).startsWith("/api/chats")
          ? new Response(JSON.stringify([]), { status: 200 })
          : new Response(JSON.stringify({ sessions: [] }), { status: 200 }),
      ),
    );
    useEventStore.setState({
      connected: true,
      activeSection: "board",
      conversations: [],
      messages: [],
      activeThreadId: "old-voice-thread",
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    // The event store is module-global: leaving a fetched-in list behind
    // would break whichever test file runs next.
    useEventStore.setState({ conversations: [], messages: [], activeThreadId: null });
  });

  test("on the voice stage it starts a voice run and stays on Voice", async () => {
    useHomeStore.setState({
      surface: "voice",
      transcript: [{ id: "l1", who: "user", text: "hello", ts: 1 }],
    });

    renderSidebar();
    const button = screen.getByTestId("sidebar-new-chat");
    expect(button.textContent).toContain("New voice chat");

    await act(async () => {
      button.click();
      await Promise.resolve();
    });

    expect(useHomeStore.getState().surface).toBe("voice");
    expect(useHomeStore.getState().transcript).toEqual([]);
    // The open voice thread is dropped so the next spoken turn is its own.
    expect(useEventStore.getState().activeThreadId).toBeNull();
    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls.some((c) => String(c[0]) === "/api/chats/voice/new")).toBe(true);
  });

  test("on the chat surface it still opens an empty chat", async () => {
    useHomeStore.setState({ surface: "chat", transcript: [] });

    renderSidebar();
    const button = screen.getByTestId("sidebar-new-chat");
    expect(button.textContent).toContain("New chat");
    expect(button.textContent).not.toContain("voice");

    await act(async () => {
      button.click();
      await Promise.resolve();
    });

    expect(useHomeStore.getState().surface).toBe("chat");
    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls.some((c) => String(c[0]) === "/api/chats/voice/new")).toBe(false);
  });
});

describe("Sidebar header avatar", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: true,
      assistantName: "Ruben",
    });
  });

  afterEach(() => {
    cleanup();
    overlayMock.style = "jarvis_bar";
  });

  // The mark is a bundled import, so its URL carries a build hash and there is
  // nothing stable to assert. What matters is that the avatar shows the mark
  // and not a stale public/ path a browser would serve from cache.
  test("renders the Gigi app mark from the bundle, not a public path", () => {
    const { container } = renderSidebar();
    const avatar = container.querySelector('[data-testid="sidebar-style-avatar"]');
    expect(avatar).not.toBeNull();
    expect(avatar?.getAttribute("data-variant")).toBe("logo");
    const logo = avatar?.querySelector("img") as HTMLImageElement;
    const src = logo.getAttribute("src") ?? "";
    expect(src).toContain("jarvis-mark");
    expect(src.startsWith("/jarvis-")).toBe(false);
  });
});

describe("Sidebar brain footer", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: true,
      brainProvider: "unknown",
      brainModel: "",
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("renders the active provider and its model id", () => {
    // The footer must show WHICH model is in use, not just the provider — a
    // user who configured e.g. opus-4-8 wants that surfaced, not a bare "—".
    useEventStore.setState({ brainProvider: "claude-api", brainModel: "claude-opus-4-8" });

    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(screen.getByText("Claude (API)")).toBeTruthy();
    const modelLine = screen.getByTestId("sidebar-brain-model");
    expect(modelLine.textContent).toBe("claude-opus-4-8");
  });

  test("hides the model line when no model is known (shows provider only)", () => {
    useEventStore.setState({ brainProvider: "gemini", brainModel: "" });

    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(screen.getByText("Gemini")).toBeTruthy();
    expect(screen.queryByTestId("sidebar-brain-model")).toBeNull();
  });

  test("follows a live model change", () => {
    useEventStore.setState({ brainProvider: "claude-api", brainModel: "claude-opus-4-8" });
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));
    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe("claude-opus-4-8");

    act(() => {
      useEventStore.setState({ brainProvider: "gemini", brainModel: "gemini-3.1-flash" });
    });

    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe("gemini-3.1-flash");
    expect(screen.getByText("Gemini")).toBeTruthy();
  });
});

describe("Sidebar footer in realtime voice mode", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: true,
      // The pipeline brain stays configured — it must NOT be what the footer
      // shows while the realtime engine owns the voice path.
      brainProvider: "openrouter",
      brainModel: "google/gemini-3.5-flash",
    });
  });

  afterEach(() => {
    cleanup();
    resetVoiceModeMock();
  });

  test("shows the realtime provider + model instead of the dormant pipeline brain", () => {
    // The bug: the footer said "OpenRouter / google/gemini-3.5-flash" while
    // Gemini Live was doing all the talking. In realtime mode the card must
    // follow the realtime engine.
    voiceModeMock.value = {
      ...voiceModeMock.value,
      mode: "realtime",
      activeProvider: "gemini-live",
      activeProviderLabel: "Gemini Live",
      activeModel: "gemini-3.1-flash-live-preview",
    };

    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(screen.getByTestId("sidebar-footer-tier").textContent).toBe("Realtime");
    expect(screen.getByText("Gemini Live")).toBeTruthy();
    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe(
      "gemini-3.1-flash-live-preview",
    );
    expect(screen.queryByText("OpenRouter")).toBeNull();
    expect(screen.queryByText("google/gemini-3.5-flash")).toBeNull();
  });

  test("a RUNNING realtime session's live provider/model outrank the configured pick", () => {
    // Mid-call cross-family fallback (AP-22) must be visible: the session
    // crossed from Gemini to OpenAI, so the card shows the live engine.
    voiceModeMock.value = {
      ...voiceModeMock.value,
      mode: "realtime",
      activeProvider: "gemini-live",
      activeProviderLabel: "Gemini Live",
      activeModel: "gemini-3.1-flash-live-preview",
      sessionActive: true,
      activeSessionMode: "realtime",
      activeSessionProvider: "openai-realtime",
      activeSessionModel: "gpt-realtime-2.1",
    };

    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(screen.getByText("OpenAI Realtime")).toBeTruthy();
    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe("gpt-realtime-2.1");
  });

  test("pipeline mode keeps the classic brain footer", () => {
    // Guard the split itself: mode "pipeline" must still show the brain card
    // even when a realtime provider is fully configured.
    voiceModeMock.value = {
      ...voiceModeMock.value,
      mode: "pipeline",
      activeProvider: "gemini-live",
      activeProviderLabel: "Gemini Live",
      activeModel: "gemini-3.1-flash-live-preview",
    };

    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(screen.getByTestId("sidebar-footer-tier").textContent).toBe("Brain");
    expect(screen.getByText("OpenRouter")).toBeTruthy();
    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe(
      "google/gemini-3.5-flash",
    );
  });

  test("Vertex AI Live is named as such, not as the pipeline brain", () => {
    voiceModeMock.value = {
      ...voiceModeMock.value,
      mode: "realtime",
      activeProvider: "vertex-live",
      activeProviderLabel: "Vertex AI Live",
      activeModel: "gemini-live-2.5-flash-preview-native-audio-dialog",
    };

    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(screen.getByTestId("sidebar-footer-tier").textContent).toBe("Realtime");
    expect(screen.getByText("Vertex AI Live")).toBeTruthy();
    expect(screen.queryByText("OpenRouter")).toBeNull();
  });
});

describe("Sidebar assistant name header", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: true,
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("renders the resolved assistant name (not a hardcoded 'Jarvis')", () => {
    // The header wordmark must follow the configured assistant name so a user
    // who renames the assistant (e.g. to "Ruben") never sees a stale "Jarvis".
    useEventStore.setState({ assistantName: "Ruben" });

    renderSidebar();

    expect(screen.getByText("Ruben")).toBeTruthy();
    expect(screen.queryByText("Jarvis")).toBeNull();
  });

  test("follows a live assistant-name change", () => {
    useEventStore.setState({ assistantName: "Nova" });
    renderSidebar();
    expect(screen.getByText("Nova")).toBeTruthy();

    act(() => {
      useEventStore.setState({ assistantName: "Athena" });
    });

    expect(screen.getByText("Athena")).toBeTruthy();
    expect(screen.queryByText("Nova")).toBeNull();
  });
});

describe("Sidebar plugin reconnect indicator", () => {
  beforeEach(() => {
    useEventStore.setState({ connected: true, voiceReady: true });
  });

  afterEach(() => {
    cleanup();
    pluginAttentionMock.needsReconnect = false;
  });

  test("shows an amber dot on Plugins, MCPs & Skills when a plugin needs reconnect", () => {
    // A revoked / expired plugin must be visible app-wide, not only on the
    // Plugins page — the sidebar carries an amber dot on the row that fronts
    // Plugins ("Plugins, MCPs & Skills", id "plugins").
    pluginAttentionMock.needsReconnect = true;

    renderSidebar();

    expect(screen.getByTestId("nav-warn-plugins")).toBeTruthy();
  });

  test("no amber dot when every plugin is healthy", () => {
    pluginAttentionMock.needsReconnect = false;

    renderSidebar();

    expect(screen.queryByTestId("nav-warn-plugins")).toBeNull();
  });
});

describe("Sidebar voice-boot indicator", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: false,
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("shows a 'Voice starting…' spinner while connected but voice not ready", () => {
    // The window connects in ~1s but the voice feature warms up ~20s in the
    // background. During that gap the header must signal "starting", not the
    // normal idle "Ready" state (which would imply the mic already works).
    useEventStore.setState({ connected: true, voiceReady: false });

    const { container } = renderSidebar();

    expect(screen.getByText("Voice starting…")).toBeTruthy();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).not.toBeNull();
    // The normal idle voice label must NOT be shown during warmup.
    expect(screen.queryByText("Ready")).toBeNull();
  });

  test("reverts to the normal voice state once voice is ready", () => {
    useEventStore.setState({ connected: true, voiceReady: true, voiceState: "idle" });

    const { container } = renderSidebar();

    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.queryByText("Voice starting…")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).toBeNull();
  });

  test("shows 'Offline' (not the spinner) when disconnected and NOT warming", () => {
    // Truly offline: no live socket AND the WS is not in the fast-boot warming
    // loop (no 1013) — the honest state is Offline.
    useEventStore.setState({ connected: false, voiceReady: false, wsWarming: false });

    const { container } = renderSidebar();

    expect(screen.getByText("Offline")).toBeTruthy();
    expect(screen.queryByText("Voice starting…")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).toBeNull();
  });

  test("shows the booting label + spinner (not Offline) while warming", () => {
    // Disconnected but the fast-boot bootstrap keeps closing the WS with 1013:
    // the backend is still starting, so the honest state is "Starting…", not
    // the alarming "Offline".
    useEventStore.setState({ connected: false, voiceReady: false, wsWarming: true });

    const { container } = renderSidebar();

    expect(screen.getByText("Starting…")).toBeTruthy();
    expect(screen.queryByText("Offline")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).not.toBeNull();
  });
});

/*
 * The icon rail — what the sidebar becomes when it is dragged in.
 *
 * The seam used to stop at 200 px, which is still wide enough to read every
 * label; in the Agentic IDE that meant a fifth of the window stayed spent on a
 * nav list nobody was reading while a dozen terminals fought over the rest. Two
 * things have to hold for the rail to be a sidebar rather than a broken one:
 * every destination is still REACHABLE, and every icon still SAYS what it is.
 * A refactor that quietly drops either turns the rail into a column of mystery
 * glyphs, and nothing else on screen would look wrong.
 *
 * Anchored on the row's test id rather than its text: the label is translated,
 * so asserting on it would make these pass or fail with the active locale.
 */
describe("Sidebar icon rail", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      activeSection: "chats",
    });
  });

  afterEach(() => cleanup());

  test("shows its labels at the designed width", () => {
    renderSidebar(SIDEBAR_DEFAULT_WIDTH);

    expect(screen.getByTestId("sidebar").dataset.railed).toBe("false");
    fireEvent.click(screen.getByTestId("sidebar-tools-toggle"));
    // The label is ON the row, and names the workspace rather than carrying
    // the retired generic "Chat" label shown in the product screenshot.
    expect(screen.getByTestId("nav-row-agentic-ide").textContent).toContain(
      "Agentic IDE",
    );
  });

  test("drops to icons once dragged past the snap point", () => {
    renderSidebar(SIDEBAR_RAIL_AT_WIDTH - 1);

    const aside = screen.getByTestId("sidebar");
    expect(aside.dataset.railed).toBe("true");
    // Snapped, not clipped: the band between the rail and a readable sidebar
    // shows half a word per row and reads as a rendering fault, so it is
    // skipped rather than rendered at the dragged width.
    expect(aside.style.width).toBe(`${SIDEBAR_RAIL_WIDTH}px`);
    expect(screen.getByTestId("nav-row-agents").getAttribute("aria-label")).toBeTruthy();
  });

  test("keeps every destination named once its label is off the screen", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    const row = screen.getByTestId("nav-row-agents");
    expect(row.getAttribute("aria-label")).toBeTruthy();
    expect(row.getAttribute("title")).toBe(row.getAttribute("aria-label"));
  });

  test("still switches section on a click", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    act(() => {
      screen.getByTestId("nav-row-agents").click();
    });

    expect(useEventStore.getState().activeSection).toBe("agents");
  });

  test("keeps the rail canvas transparent and the active control glassy", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar.querySelector(".jarvis-shell-surface")).toBeNull();
    expect(screen.getByTestId("nav-row-chats").classList).toContain(
      "jarvis-nav-active",
    );
  });

  test("keeps the wake-word hint and realtime control off the rail", () => {
    // Both are label-shaped controls that cannot say anything useful in 64 px.
    // They step aside rather than being clipped into unreadable stubs.
    useEventStore.setState({
      voiceState: "listening",
      transcription: "auflegen",
      transcriptionFinal: false,
    });

    renderSidebar(SIDEBAR_RAIL_WIDTH);

    expect(screen.queryByText("auflegen")).toBeNull();
    // …and the navigation, which is the reason the rail exists, is still there.
    expect(screen.getByTestId("nav-row-chats")).toBeTruthy();
  });
});

/**
 * The explicit collapse toggle.
 *
 * The rail used to be reachable only by dragging the seam far enough — a
 * gesture nobody discovers, on a seam one pixel wide. The button says the same
 * thing out loud, and it is what the app opens in (see `App.tsx`); the drag
 * stays as the second, finer way in.
 */
describe("Sidebar collapse toggle", () => {
  beforeEach(() => {
    useEventStore.setState({ activeSection: "chats", connected: true });
  });

  afterEach(() => cleanup());

  function renderWithToggle(collapsed: boolean, onToggle = () => {}) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <Sidebar
          width={SIDEBAR_DEFAULT_WIDTH}
          collapsed={collapsed}
          onToggleCollapsed={onToggle}
        />
      </QueryClientProvider>,
    );
  }

  test("collapses to the rail even at a wide dragged width", () => {
    renderWithToggle(true);

    const aside = screen.getByTestId("sidebar");
    // The collapse is a STATE, not a width: the dragged 280 px is remembered
    // for the expand, and the rail wins while collapsed.
    expect(aside.dataset.railed).toBe("true");
    expect(aside.style.width).toBe(`${SIDEBAR_RAIL_WIDTH}px`);
  });

  test("reports its state and names itself in both directions", () => {
    const { rerender } = renderWithToggle(true);

    const collapsedButton = screen.getByTestId("sidebar-collapse-toggle");
    expect(collapsedButton.getAttribute("aria-expanded")).toBe("false");
    // WHAT it says is the locale's business; that it says something is not.
    expect(collapsedButton.getAttribute("aria-label")).toBeTruthy();

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    rerender(
      <QueryClientProvider client={client}>
        <Sidebar
          width={SIDEBAR_DEFAULT_WIDTH}
          collapsed={false}
          onToggleCollapsed={() => {}}
        />
      </QueryClientProvider>,
    );

    expect(
      screen.getByTestId("sidebar-collapse-toggle").getAttribute("aria-expanded"),
    ).toBe("true");
  });

  test("asks the shell to toggle rather than deciding for itself", () => {
    // The width and the collapsed flag live together in the shell — a sidebar
    // that flipped its own state would drift from the seam beside it.
    const onToggle = vi.fn();
    renderWithToggle(true, onToggle);

    act(() => {
      screen.getByTestId("sidebar-collapse-toggle").click();
    });

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  test("is simply absent when the shell offers no toggle", () => {
    // Standalone renders (tests, any future embedding) must not grow a button
    // that cannot do anything.
    renderSidebar(SIDEBAR_DEFAULT_WIDTH);

    expect(screen.queryByTestId("sidebar-collapse-toggle")).toBeNull();
  });
});

describe("compact sidebar navigation", () => {
  beforeEach(() => {
    useEventStore.setState({ activeSection: "chats", connected: true });
    useIdeChatStore.setState({ workspaces: [], view: "chat" });
  });
  afterEach(() => cleanup());

  test("keeps core destinations above visible recent chats", () => {
    renderSidebar();
    for (const id of ["agents", "visualization", "tasks", "plugins", "marketplace"]) {
      expect(screen.getByTestId(`nav-row-${id}`)).toBeTruthy();
    }
    expect(screen.getByTestId("recent-chats")).toBeTruthy();
    expect(screen.queryByTestId("nav-row-wallpaper")).toBeNull();
    expect(screen.queryByTestId("nav-row-memory")).toBeNull();
  });

  test("expands tools through More without duplicating rows", () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-tools-toggle"));
    expect(screen.getByTestId("nav-row-memory")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sidebar-more-toggle"));
    expect(screen.getAllByTestId("nav-row-memory")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("nav-row-memory"));
    expect(useEventStore.getState().activeSection).toBe("memory");
    fireEvent.click(screen.getByTestId("sidebar-more-toggle"));
    expect(screen.queryByTestId("nav-row-memory")).toBeNull();
    expect(screen.getByTestId("recent-chats")).toBeTruthy();
  });

  test("opens settings from the profile and closes after navigation", () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));
    fireEvent.click(screen.getByTestId("nav-row-wallpaper"));
    expect(useEventStore.getState().activeSection).toBe("wallpaper");
    expect(screen.queryByTestId("sidebar-profile-panel")).toBeNull();
  });

  test("dismisses the profile with Escape and restores focus", () => {
    renderSidebar();
    const trigger = screen.getByTestId("sidebar-profile-toggle");
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("sidebar-profile-panel")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByTestId("sidebar-profile-panel")).toBeNull();
  });

  test("every registered destination remains reachable", () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-more-toggle"));
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));
    for (const item of [...NAV_GROUPS.flat(), ...NAV_FOOTER_ITEMS]) {
      expect(screen.getByTestId(`nav-row-${item.id}`)).toBeTruthy();
    }
  });
});
