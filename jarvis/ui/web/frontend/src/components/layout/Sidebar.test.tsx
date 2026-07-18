import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { Sidebar } from "@/components/layout/Sidebar";
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

function renderSidebar() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <Sidebar />
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
