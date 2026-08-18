import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

// The orb's face is canvas + the mascot's timers — both proven elsewhere
// (VoiceOrb.test, MascotGigi.test). Here only the press affordance matters.
vi.mock("@/components/agentic/VoiceOrb", () => ({
  VoiceOrb: () => <div data-testid="voice-orb" />,
}));
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div data-testid="mascot" />,
}));

import { DeckOrb } from "@/components/deck/DeckOrb";

/**
 * The orb is the click-shaped wake word (maintainer, 2026-08-18): pressing
 * the mascot in the centre does what saying the phrase does. Display-only
 * callers get no button at all.
 */
describe("DeckOrb", () => {
  afterEach(() => cleanup());

  test("is display only without a press handler", () => {
    render(<DeckOrb steps={[]} busy={false} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByTestId("mascot")).toBeTruthy();
  });

  test("a press on the mascot fires the handler and carries its label", () => {
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
    fireEvent.click(screen.getByTestId("mascot"));
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
