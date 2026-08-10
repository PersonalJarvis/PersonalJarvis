import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  Sidebar,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_RAIL_AT_WIDTH,
  SIDEBAR_RAIL_WIDTH,
} from "@/components/layout/Sidebar";
import { useEventStore } from "@/store/events";

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

  test("still shows the live transcription in its own box while listening", () => {
    // The transcript is already surfaced by the sidebar's dedicated box, so
    // dropping the mascot bubble loses no information.
    useEventStore.setState({
      voiceState: "listening",
      transcription: "auflegen",
      transcriptionFinal: false,
    });

    renderSidebar();

    // getByText throws if absent or if it matches more than once — so a single
    // hit proves the transcript survives exactly once (no duplicate bubble).
    expect(screen.getByText("auflegen")).toBeTruthy();
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

  // NOTE: an earlier change had the header avatar mirror the overlay display
  // style (bar glyph for "jarvis_bar"). A later snapshot reverted it to the
  // canonical static brand logo (jarvis-logo.png) regardless of style. This
  // test pins the CURRENT behavior; the bar-vs-mascot-vs-logo choice is a
  // product/branding decision tracked separately from the boot-speed work.
  test("renders the static brand-logo avatar (one stable header identity)", () => {
    const { container } = renderSidebar();
    const avatar = container.querySelector('[data-testid="sidebar-style-avatar"]');
    expect(avatar).not.toBeNull();
    expect(avatar?.getAttribute("data-variant")).toBe("logo");
  });

  test("retries a failed logo load with a cache-busted URL (self-healing)", () => {
    // A load that fails once (backend restarting, dist mid-rebuild) must not
    // stick as the browser's broken-image glyph forever: after an error the
    // <img> re-requests the logo under a cache-busting query.
    vi.useFakeTimers();
    try {
      const { container } = renderSidebar();
      const logo = container.querySelector(
        '[data-testid="sidebar-style-avatar"] img',
      ) as HTMLImageElement;
      expect(logo.getAttribute("src")).toBe("/jarvis-logo.png");

      act(() => {
        logo.dispatchEvent(new Event("error"));
        vi.runAllTimers();
      });

      expect(logo.getAttribute("src")).toBe("/jarvis-logo.png?retry=1");
    } finally {
      vi.useRealTimers();
    }
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

    expect(screen.getByText("Claude (API)")).toBeTruthy();
    const modelLine = screen.getByTestId("sidebar-brain-model");
    expect(modelLine.textContent).toBe("claude-opus-4-8");
  });

  test("hides the model line when no model is known (shows provider only)", () => {
    useEventStore.setState({ brainProvider: "gemini", brainModel: "" });

    renderSidebar();

    expect(screen.getByText("Gemini")).toBeTruthy();
    expect(screen.queryByTestId("sidebar-brain-model")).toBeNull();
  });

  test("follows a live model change", () => {
    useEventStore.setState({ brainProvider: "claude-api", brainModel: "claude-opus-4-8" });
    renderSidebar();
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

    expect(screen.getByTestId("sidebar-footer-tier").textContent).toBe("Brain");
    expect(screen.getByText("OpenRouter")).toBeTruthy();
    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe(
      "google/gemini-3.5-flash",
    );
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

  test("shows an amber dot on Skills & Tools when a plugin needs reconnect", () => {
    // A revoked / expired plugin must be visible app-wide, not only on the
    // Plugins page — the sidebar carries an amber dot on the row that fronts
    // Plugins ("Skills & Tools", id "skills").
    pluginAttentionMock.needsReconnect = true;

    renderSidebar();

    expect(screen.getByTestId("nav-warn-skills")).toBeTruthy();
  });

  test("no amber dot when every plugin is healthy", () => {
    pluginAttentionMock.needsReconnect = false;

    renderSidebar();

    expect(screen.queryByTestId("nav-warn-skills")).toBeNull();
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
    expect(screen.getByTestId("nav-row-agentic-ide").textContent?.trim()).toBe(
      "",
    );
  });

  test("keeps every destination named once its label is off the screen", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    // The label survives as the accessible name and as the hover text — it is
    // off the screen, not gone. Both non-empty is the assertion; WHAT they say
    // is the locale's business.
    const row = screen.getByTestId("nav-row-agentic-ide");
    expect(row.getAttribute("aria-label")).toBeTruthy();
    expect(row.getAttribute("title")).toBeTruthy();
  });

  test("still switches section on a click", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    act(() => {
      screen.getByTestId("nav-row-agentic-ide").click();
    });

    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
  });

  test("keeps the rail canvas transparent and the active control glassy", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar.querySelector(".jarvis-shell-surface")).toBeNull();
    expect(screen.getByTestId("nav-row-chats").classList).toContain(
      "jarvis-message-surface",
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
