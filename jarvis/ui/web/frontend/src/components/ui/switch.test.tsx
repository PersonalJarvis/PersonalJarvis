/**
 * Regression guard for the two ways this control has been wrong.
 *
 * First it was invisible: on the near-black theme the unchecked track used
 * --input and the thumb used --background, both within a few values of the
 * page, so an OFF switch vanished and the one disabled skill looked as though
 * it had no toggle at all.
 *
 * Then it read backwards: ON was a mid grey while OFF carried the brightest
 * pixel in the row, so a disabled setting drew more attention than an enabled
 * one. ON is now a life signal (--success) and OFF a resting lift surface
 * (--secondary), with the thumb as the ink of whichever track it rides on.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { Switch } from "@/components/ui/switch";

afterEach(cleanup);

function renderSwitch(checked: boolean) {
  const { container } = render(
    <Switch checked={checked} onCheckedChange={() => {}} />,
  );
  const root = container.querySelector('[role="switch"]') as HTMLElement;
  expect(root).toBeTruthy();
  const thumb = root.querySelector("span") as HTMLElement;
  expect(thumb).toBeTruthy();
  return { root, thumb };
}

describe("Switch state legibility", () => {
  it("never paints a track with a ground the page already uses", () => {
    const { root } = renderSwitch(false);
    expect(root.className).not.toContain("bg-input");
    expect(root.className).not.toContain("bg-background");
    expect(root.className).not.toContain("bg-card");
  });

  it("rests OFF on the lift surface and ON on the life colour", () => {
    const { root } = renderSwitch(false);
    expect(root.className).toContain("data-[state=unchecked]:bg-secondary");
    expect(root.className).toContain("data-[state=checked]:bg-success");
  });

  it("keeps the thumb readable on both tracks without outshining the row", () => {
    const { thumb } = renderSwitch(true);
    expect(thumb.className).toContain("data-[state=checked]:bg-primary-foreground");
    expect(thumb.className).toContain("data-[state=unchecked]:bg-muted-foreground");
    // The brightest pixel in the row must never be an OFF switch.
    expect(thumb.className).not.toContain("data-[state=unchecked]:bg-foreground");
  });
});
