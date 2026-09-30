import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { BEAT_IDS, beatsFor, CARD_WIDTH, MASCOT_CUE, nextBeat, previousBeat, resumeBeat } from "./beats";

describe("beat order", () => {
  it("matches ONBOARDING_STEPS in the backend", () => {
    const py = readFileSync(
      join(__dirname, "..", "..", "..", "..", "..", "..", "setup", "onboarding_meta.py"),
      "utf8",
    );
    const block = /ONBOARDING_STEPS: list\[str\] = \[([\s\S]*?)\]/.exec(py);
    expect(block).not.toBeNull();
    const backend = [...block![1].matchAll(/"([a-z-]+)"/g)].map((m) => m[1]);
    expect(backend).toEqual([...BEAT_IDS]);
  });

  it("asks for permissions on macOS only", () => {
    expect(beatsFor("darwin")).toContain("permissions");
    expect(beatsFor("win32")).not.toContain("permissions");
    expect(beatsFor("linux")).not.toContain("permissions");
    // A failed platform probe leaves it out — Settings is the recovery path.
    expect(beatsFor(null)).not.toContain("permissions");
  });

  it("walks forward and back", () => {
    const beats = beatsFor("win32");
    expect(nextBeat(beats, "welcome")).toBe("brain");
    expect(nextBeat(beats, "agents")).toBe("voice");
    expect(nextBeat(beats, "ready")).toBeNull();
    expect(previousBeat(beats, "brain")).toBe("welcome");
    expect(previousBeat(beats, "welcome")).toBeNull();
  });

  it("gives every beat a width and a mascot cue", () => {
    for (const beat of BEAT_IDS) {
      expect(CARD_WIDTH[beat]).toBeGreaterThan(400);
      expect(MASCOT_CUE[beat]).toBeTruthy();
    }
  });
});

describe("resumeBeat", () => {
  const beats = beatsFor("win32");

  it("never skips the consent", () => {
    expect(resumeBeat(beats, "voice", false)).toBe("welcome");
  });

  it("returns to the saved beat once consent exists", () => {
    expect(resumeBeat(beats, "voice", true)).toBe("voice");
  });

  it("starts after the consent when the saved beat is unknown", () => {
    // e.g. a step id from the previous guide (`api-keys`, `wake-word`).
    expect(resumeBeat(beats, "api-keys", true)).toBe("brain");
    expect(resumeBeat(beats, null, true)).toBe("brain");
  });
});
