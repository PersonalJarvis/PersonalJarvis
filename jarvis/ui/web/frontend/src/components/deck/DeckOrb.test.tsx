import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

// framer-motion's motion.img is a plain <img> for these assertions.
vi.mock("framer-motion", async () => {
  const actual = await vi.importActual<typeof import("framer-motion")>("framer-motion");
  return {
    ...actual,
    useReducedMotion: () => true,
  };
});

import { DeckOrb } from "@/components/deck/DeckOrb";

/**
 * The orb is the click-shaped wake word (maintainer, 2026-08-18): pressing
 * the orb in the centre does what saying the phrase does. Display-only
 * callers get no button at all. The orb is the product's own artwork — the
 * sphere cut out of `hero-orb.png` — and nothing else in its core: the mascot
 * that rode there for a day made two ghosts on one stage next to the live
 * wallpaper mascot (maintainer, 2026-08-19). It carries the live voice state
 * so it can breathe with it.
 */
describe("DeckOrb", () => {
  afterEach(() => cleanup());

  test("is display only without a press handler", () => {
    render(<DeckOrb steps={[]} busy={false} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByTestId("jarvis-orb")).toBeTruthy();
  });

  test("the centre is the Jarvis orb artwork, and nothing rides in its core", () => {
    render(<DeckOrb steps={[]} busy={false} />);
    const orb = screen.getByTestId("jarvis-orb");
    expect(orb.getAttribute("data-voice")).toBe("idle");
    const images = orb.querySelectorAll("img");
    expect(images).toHaveLength(1);
    expect(images[0].getAttribute("src")).toBe("/deck-orb.png");
    // Artwork, not content: nothing here is read out.
    expect(images[0].getAttribute("alt")).toBe("");
  });

  test("a press on the orb fires the handler and carries its label", () => {
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
