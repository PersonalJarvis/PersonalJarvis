import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { resumeStep, SETUP_STEP_IDS, SETUP_STEPS, stepsFor } from "./setupSteps";

const SRC = join(__dirname, "..", "..", "..");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

describe("setup steps", () => {
  it("match ONBOARDING_STEPS in the backend", () => {
    const py = readFileSync(join(SRC, "..", "..", "..", "..", "setup", "onboarding_meta.py"), "utf8");
    const block = /ONBOARDING_STEPS: list\[str\] = \[([\s\S]*?)\]/.exec(py);
    expect(block).not.toBeNull();
    expect([...block![1].matchAll(/"([a-z-]+)"/g)].map((m) => m[1])).toEqual([...SETUP_STEP_IDS]);
  });

  it("have no permissions step: macOS asks where a feature needs it", () => {
    expect(stepsFor()).toEqual(["welcome", "keys", "subscriptions", "voice", "ready"]);
    expect([...SETUP_STEP_IDS]).not.toContain("permissions");
    expect(Object.keys(SETUP_STEPS)).not.toContain("permissions");
  });

  it("start with the consent and end with the start", () => {
    const steps = stepsFor();
    expect(steps[0]).toBe("welcome");
    expect(steps[steps.length - 1]).toBe("ready");
  });

  it("point only at anchors the app actually sets", () => {
    const code = sources(SRC)
      .filter((f) => !f.includes(join("components", "onboarding")))
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    for (const id of SETUP_STEP_IDS) {
      const anchor = SETUP_STEPS[id].anchor;
      if (!anchor) continue;
      const literal = code.includes(`data-tour="${anchor}"`);
      // Settings groups get theirs from a template: data-tour={`settings-${section.id}`}.
      const group = anchor.startsWith("settings-") && code.includes("data-tour={`settings-${section.id}`}");
      expect(literal || group, anchor).toBe(true);
    }
  });

  it("open the app's own place for each job", () => {
    expect(SETUP_STEPS.keys.section).toBe("apikeys");
    expect(SETUP_STEPS.subscriptions.section).toBe("apikeys");
    expect(SETUP_STEPS.subscriptions.apiKeysTab).toBe("subagents");
    expect(SETUP_STEPS.voice.section).toBe("settings");
    expect(SETUP_STEPS.voice.anchor).toBe("settings-wake-word");
    expect(SETUP_STEPS.welcome.anchor).toBeUndefined();
  });
});

describe("resumeStep", () => {
  const steps = stepsFor();

  it("never skips the consent", () => {
    expect(resumeStep(steps, "voice", false)).toBe("welcome");
  });

  it("returns to the saved step once consent exists", () => {
    expect(resumeStep(steps, "voice", true)).toBe("voice");
  });

  it("maps a stored legacy permissions step to the voice step on every OS", () => {
    // An older build stored "permissions" (macOS only) right before "voice".
    expect(resumeStep(steps, "permissions", true)).toBe("voice");
    // Still never past the consent.
    expect(resumeStep(steps, "permissions", false)).toBe("welcome");
  });

  it("starts after the consent for an unknown or old step id", () => {
    expect(resumeStep(steps, "api-keys", true)).toBe("keys");
    expect(resumeStep(steps, null, true)).toBe("keys");
    expect(resumeStep(steps, "welcome", true)).toBe("keys");
  });
});
