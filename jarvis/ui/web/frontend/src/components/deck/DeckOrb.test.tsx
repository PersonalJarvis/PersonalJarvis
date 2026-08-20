import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

// framer-motion is real here; only the reduced-motion hook is pinned, so the
// voice loops do not run during the assertions.
vi.mock("framer-motion", async () => {
  const actual = await vi.importActual<typeof import("framer-motion")>("framer-motion");
  return {
    ...actual,
    useReducedMotion: () => true,
  };
});

import { DeckOrb } from "@/components/deck/DeckOrb";
import { useEventStore } from "@/store/events";
import { clearVoiceInputLevel, setVoiceInputLevel } from "@/lib/voiceInputLevel";
import type { ThinkingStep } from "@/lib/thinkingSteps";

const step = (id: string, status: ThinkingStep["status"] = "active"): ThinkingStep => ({
  id,
  kind: "tool",
  labelKey: "thinking.tool",
  status,
  startedTs: 0,
});

/**
 * The centre is a STAGE, not a reticle (maintainer, 2026-08-20: "a circle
 * around the mascot makes no sense"). The mascot stands free, lit from
 * behind and below; the meter is a wave under it; the readouts are one row.
 * Pressing the figure does what saying the wake phrase does; display-only
 * callers get no button at all.
 */
describe("DeckOrb", () => {
  afterEach(() => cleanup());

  test("is display only without a press handler", () => {
    render(<DeckOrb steps={[]} busy={false} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByTestId("jarvis-orb")).toBeTruthy();
  });

  test("nothing rings the figure — the light is behind it and under it", () => {
    const { container } = render(<DeckOrb steps={[]} busy={false} />);
    // The sphere's furniture: bezel circle, dial ticks, the arc meter, the
    // ripple rings, the corona. All of it went with the sphere.
    expect(container.querySelectorAll("line")).toHaveLength(0);
    expect(screen.queryByTestId("deck-orb-vu")).toBeNull();
    expect(screen.queryByTestId("deck-orb-ripples")).toBeNull();
    expect(container.querySelector(".deck-orb-corona")).toBeNull();
    expect(container.querySelector(".deck-orb-glow")).toBeNull();
    // What stages the figure now.
    expect(screen.getByTestId("deck-stage-light")).toBeTruthy();
    expect(screen.getByTestId("deck-stage-footlight")).toBeTruthy();
    expect(screen.getByTestId("deck-orb-wave")).toBeTruthy();
  });

  test("the readouts are one row, not four corners", () => {
    render(
      <DeckOrb
        steps={[]}
        busy={false}
        readouts={{ nw: "Ready", ne: "0 steps", sw: "Vertex AI", se: "0 words" }}
      />,
    );
    const row = screen.getByTestId("deck-orb-readouts");
    // Engine first — the one a person checks before they speak.
    expect(row.textContent).toContain("Vertex AI");
    expect(row.textContent).toContain("Ready");
    expect(row.textContent).toContain("0 steps");
    expect(row.textContent).toContain("0 words");
    expect(screen.getByTestId("deck-orb-provider").textContent).toBe("Vertex AI");
  });

  test("parallel work shows as marks, and nothing at all when none runs", () => {
    const { rerender } = render(<DeckOrb steps={[]} busy={false} />);
    expect(screen.queryByTestId("deck-orb-steps")).toBeNull();
    rerender(<DeckOrb steps={[step("a"), step("b", "done")]} busy />);
    expect(screen.getByTestId("deck-orb-steps").childElementCount).toBe(2);
    // The wave only travels while work is running.
    expect(screen.getByTestId("deck-orb-wave").getAttribute("data-busy")).toBe("true");
    rerender(<DeckOrb steps={[]} busy={false} />);
    expect(screen.getByTestId("deck-orb-wave").getAttribute("data-busy")).toBeNull();
  });

  test("moves with the voice: the real microphone level, one variable for all", async () => {
    useEventStore.setState({ voiceState: "listening" });
    setVoiceInputLevel(0.8, "native");
    try {
      render(<DeckOrb steps={[]} busy={false} />);
      const root = screen.getByTestId("deck-orb");
      await waitFor(() => {
        expect(Number(root.style.getPropertyValue("--orb-level"))).toBeGreaterThan(0.3);
      });
    } finally {
      clearVoiceInputLevel();
      useEventStore.setState({ voiceState: "idle" });
    }
  });

  test("the centre is the mascot itself, drawn as vectors — no bitmap left", () => {
    render(<DeckOrb steps={[]} busy={false} />);
    const orb = screen.getByTestId("jarvis-orb");
    expect(orb.getAttribute("data-voice")).toBe("idle");
    // The PNG is gone from the tree, so it cannot come back in through here.
    expect(orb.querySelectorAll("img")).toHaveLength(0);
    expect(orb.querySelector(".gigi-root")).toBeTruthy();
  });

  test("a press on the figure fires the handler and carries its label", () => {
    const onPress = vi.fn();
    render(
      <DeckOrb
        steps={[]}
        busy={false}
        onPress={onPress}
        pressLabel="Start talking — the same as saying “Hey Nova”."
      />,
    );
    const button = screen.getByRole("button", { name: /saying “Hey Nova”/ });
    expect(button.getAttribute("title")).toContain("Hey Nova");
    fireEvent.click(screen.getByTestId("jarvis-orb"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  test("stays inert while the call is being set up", () => {
    const onPress = vi.fn();
    render(
      <DeckOrb steps={[]} busy={false} onPress={onPress} pressLabel="Start" pressDisabled />,
    );
    const button = screen.getByRole("button", { name: "Start" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(onPress).not.toHaveBeenCalled();
  });
});
