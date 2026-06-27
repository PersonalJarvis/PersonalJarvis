import { act, cleanup, render, screen } from "@testing-library/react";
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

    const { container } = render(<Sidebar />);

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

    render(<Sidebar />);

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
      assistantName: "Alex",
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
    const { container } = render(<Sidebar />);
    const avatar = container.querySelector('[data-testid="sidebar-style-avatar"]');
    expect(avatar).not.toBeNull();
    expect(avatar?.getAttribute("data-variant")).toBe("logo");
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

    render(<Sidebar />);

    expect(screen.getByText("Claude (API)")).toBeTruthy();
    const modelLine = screen.getByTestId("sidebar-brain-model");
    expect(modelLine.textContent).toBe("claude-opus-4-8");
  });

  test("hides the model line when no model is known (shows provider only)", () => {
    useEventStore.setState({ brainProvider: "gemini", brainModel: "" });

    render(<Sidebar />);

    expect(screen.getByText("Gemini")).toBeTruthy();
    expect(screen.queryByTestId("sidebar-brain-model")).toBeNull();
  });

  test("follows a live model change", () => {
    useEventStore.setState({ brainProvider: "claude-api", brainModel: "claude-opus-4-8" });
    render(<Sidebar />);
    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe("claude-opus-4-8");

    act(() => {
      useEventStore.setState({ brainProvider: "gemini", brainModel: "gemini-3.1-flash" });
    });

    expect(screen.getByTestId("sidebar-brain-model").textContent).toBe("gemini-3.1-flash");
    expect(screen.getByText("Gemini")).toBeTruthy();
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
    // who renames the assistant (e.g. to "Alex") never sees a stale "Jarvis".
    useEventStore.setState({ assistantName: "Alex" });

    render(<Sidebar />);

    expect(screen.getByText("Alex")).toBeTruthy();
    expect(screen.queryByText("Jarvis")).toBeNull();
  });

  test("follows a live assistant-name change", () => {
    useEventStore.setState({ assistantName: "Jarvis" });
    render(<Sidebar />);
    expect(screen.getByText("Jarvis")).toBeTruthy();

    act(() => {
      useEventStore.setState({ assistantName: "Athena" });
    });

    expect(screen.getByText("Athena")).toBeTruthy();
    expect(screen.queryByText("Jarvis")).toBeNull();
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

    const { container } = render(<Sidebar />);

    expect(screen.getByText("Voice starting…")).toBeTruthy();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).not.toBeNull();
    // The normal idle voice label must NOT be shown during warmup.
    expect(screen.queryByText("Ready")).toBeNull();
  });

  test("reverts to the normal voice state once voice is ready", () => {
    useEventStore.setState({ connected: true, voiceReady: true, voiceState: "idle" });

    const { container } = render(<Sidebar />);

    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.queryByText("Voice starting…")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).toBeNull();
  });

  test("shows 'Offline' (not the spinner) when disconnected and NOT warming", () => {
    // Truly offline: no live socket AND the WS is not in the fast-boot warming
    // loop (no 1013) — the honest state is Offline.
    useEventStore.setState({ connected: false, voiceReady: false, wsWarming: false });

    const { container } = render(<Sidebar />);

    expect(screen.getByText("Offline")).toBeTruthy();
    expect(screen.queryByText("Voice starting…")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).toBeNull();
  });

  test("shows the booting label + spinner (not Offline) while warming", () => {
    // Disconnected but the fast-boot bootstrap keeps closing the WS with 1013:
    // the backend is still starting, so the honest state is "Starting…", not
    // the alarming "Offline".
    useEventStore.setState({ connected: false, voiceReady: false, wsWarming: true });

    const { container } = render(<Sidebar />);

    expect(screen.getByText("Starting…")).toBeTruthy();
    expect(screen.queryByText("Offline")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).not.toBeNull();
  });
});
