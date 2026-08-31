import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IdentityGlyph, identityTint } from "./shared";

describe("identityTint", () => {
  it("is stable for the same seed", () => {
    expect(identityTint("inbox-triage")).toBe(identityTint("inbox-triage"));
  });

  it("does not collapse every name onto one hue", () => {
    const seeds = [
      "inbox-triage",
      "morning-briefing",
      "x-marketing",
      "github-hooks",
      "discord-bot",
      "youtube",
      "weather",
      "standup",
    ];
    const hues = new Set(seeds.map(identityTint));
    expect(hues.size).toBeGreaterThan(3);
  });
});

describe("IdentityGlyph", () => {
  it("paints the hashed colour onto the chip", () => {
    const { container } = render(
      <IdentityGlyph seed="x-marketing" className="h-8 w-8">
        <span>X</span>
      </IdentityGlyph>,
    );
    const chip = container.firstElementChild as HTMLElement;
    expect(chip.style.backgroundColor).toBeTruthy();
    expect(chip.className).toContain("rounded-full");
  });
});
