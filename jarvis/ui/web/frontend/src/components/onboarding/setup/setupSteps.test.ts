import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/onboarding/en.json";
import { resumeStep, SETUP_STEP_IDS, WALK_STOPS, walkStopsFor, WIZARD_STEP_IDS } from "./setupSteps";

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

  it("show three steps in the window, then the walk", () => {
    expect([...WIZARD_STEP_IDS]).toEqual(["name", "connect", "voice"]);
    expect(SETUP_STEP_IDS[SETUP_STEP_IDS.length - 1]).toBe("tour");
  });
});

describe("the walk", () => {
  const stops = (en as { first_run: { tour: { stops: Record<string, string>; labels: Record<string, string> } } })
    .first_run.tour;

  it("has unique stops, each with a line and a label in the locale", () => {
    const ids = WALK_STOPS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) {
      expect(stops.stops[id], id).toBeTruthy();
      expect(stops.labels[id], id).toBeTruthy();
    }
  });

  it("explains every section the user meets", () => {
    const ids = WALK_STOPS.map((s) => s.id);
    for (const id of ["chat", "voice", "agents", "ide", "plugins", "wake"]) expect(ids).toContain(id);
  });

  it("has seven stops on every OS and no permissions stop (permissions are asked just in time)", () => {
    for (const platform of ["win32", "linux", "darwin", null]) {
      const ids = walkStopsFor(platform).map((s) => s.id);
      expect(ids.length).toBe(7);
      expect(ids).not.toContain("permissions");
    }
  });

  it("points only at anchors the app actually sets", () => {
    const code = sources(SRC)
      .filter((f) => !f.includes(join("components", "onboarding")))
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    for (const stop of WALK_STOPS) {
      if (!stop.anchor) continue;
      const literal = code.includes(`data-tour="${stop.anchor}"`);
      // Sidebar rows and Settings groups get theirs from a template.
      const nav = stop.anchor.startsWith("nav-") && code.includes("data-tour={`nav-${item.id}`}");
      const group = stop.anchor.startsWith("settings-") && code.includes("data-tour={`settings-${section.id}`}");
      expect(literal || nav || group, stop.anchor).toBe(true);
    }
  });

  it("ends on the chat", () => {
    const last = WALK_STOPS[WALK_STOPS.length - 1];
    expect(last.id).toBe("done");
    expect(last.section).toBe("chats");
  });
});

describe("resumeStep", () => {
  it("returns to the saved step", () => {
    expect(resumeStep("connect")).toBe("connect");
    expect(resumeStep("tour")).toBe("tour");
  });

  it("starts with the name for a fresh run or an unknown, old step id", () => {
    expect(resumeStep(null)).toBe("name");
    expect(resumeStep("keys")).toBe("name");
    expect(resumeStep("welcome")).toBe("name");
  });

  it("restarts a stored legacy permissions step with the name", () => {
    // An older build stored "permissions" (macOS only); that step no longer exists.
    expect(resumeStep("permissions")).toBe("name");
  });
});
