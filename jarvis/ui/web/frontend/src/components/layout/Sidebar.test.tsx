import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { Sidebar } from "@/components/layout/Sidebar";
import { useEventStore } from "@/store/events";

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

  test("shows 'Offline' (not the spinner) when disconnected", () => {
    // Disconnected outranks warmup: there is no live socket to even report
    // voice readiness, so the honest state is Offline.
    useEventStore.setState({ connected: false, voiceReady: false });

    const { container } = render(<Sidebar />);

    expect(screen.getByText("Offline")).toBeTruthy();
    expect(screen.queryByText("Voice starting…")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).toBeNull();
  });
});
