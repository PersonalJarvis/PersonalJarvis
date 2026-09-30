/**
 * The first-run guide and the app tour keep their strings in the lazy
 * `onboarding` locale chunk. Every key the code asks for must exist in
 * English, and German and Spanish must carry exactly the keys English does.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { SETUP_STEP_IDS } from "@/components/onboarding/setup/setupSteps";
import en from "./locales/onboarding/en.json";
import de from "./locales/onboarding/de.json";
import es from "./locales/onboarding/es.json";
import mainEn from "./locales/en.json";

function flatten(obj: Record<string, unknown>, prefix = ""): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flatten(v as Record<string, unknown>, key));
    } else {
      out.push(key);
    }
  }
  return out;
}

const keysOf = (loc: unknown) => new Set(flatten(loc as Record<string, unknown>));

const SRC = join(__dirname, "..");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

describe("onboarding locale chunk", () => {
  const enKeys = keysOf(en);

  for (const [lang, loc] of [["de", de], ["es", es]] as const) {
    it(`${lang} has exactly the keys en has`, () => {
      const langKeys = keysOf(loc);
      const missing = [...enKeys].filter((k) => !langKeys.has(k));
      const extra = [...langKeys].filter((k) => !enKeys.has(k));
      expect({ missing, extra }).toEqual({ missing: [], extra: [] });
    });
  }

  it("defines every literal key the code asks for", () => {
    const used = new Set<string>();
    for (const file of sources(SRC)) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(/"((?:first_run|app_tour)\.[a-z0-9_.]+)"/g)) used.add(m[1]);
    }
    expect(used.size).toBeGreaterThan(40);
    const missing = [...used].filter((k) => !enKeys.has(k));
    expect(missing).toEqual([]);
  });

  it("gives every setup step its title and lede", () => {
    for (const step of SETUP_STEP_IDS) {
      expect(enKeys.has(`first_run.${step}.title`), step).toBe(true);
      expect(enKeys.has(`first_run.${step}.lede`), step).toBe(true);
    }
  });

  it("does not collide with the startup dictionary", () => {
    // The resolver reads the startup dictionary first, so a namespace there
    // would silently shadow the chunk.
    const main = mainEn as Record<string, unknown>;
    expect(main.first_run).toBeUndefined();
    expect(main.app_tour).toBeUndefined();
    expect(main.onboarding).toBeUndefined();
  });

  it("never names the assistant with a trademark — only the {name} token", () => {
    const text = JSON.stringify(en) + JSON.stringify(de) + JSON.stringify(es);
    expect(text).not.toMatch(/\bJarvis\b/);
  });
});
