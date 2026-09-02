/**
 * Every `society.*` key the society surfaces ask for must exist in en — and
 * de/es must carry exactly the keys en carries in that block. The keys are
 * read from the source files themselves, so a roster, card or creator string
 * added without its translations fails here, not in front of a person.
 * Modelled on agent-chat-i18n-parity.test.ts.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";
// The island's strings live in the lazy `society` chunk (world README); a
// key may come from either file, and the chunk mirrors across locales too.
import enWorld from "./locales/society/en.json";
import deWorld from "./locales/society/de.json";
import esWorld from "./locales/society/es.json";

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

function blockKeys(...locs: Record<string, unknown>[]): Set<string> {
  const out = new Set<string>();
  for (const loc of locs) {
    const sub = loc.society;
    if (!sub || typeof sub !== "object") continue;
    for (const k of flatten(sub as Record<string, unknown>, "society")) out.add(k);
  }
  return out;
}

const SRC = join(__dirname, "..");
const ROOTS = [join(SRC, "components", "society"), join(SRC, "views", "society")];

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/** Literal keys (`t("society.x.y")`) plus the enum-driven prefixes (`t(\`society.state.${…}\`)`). */
function usedKeys(): { literal: Set<string>; prefixes: Set<string> } {
  const literal = new Set<string>();
  const prefixes = new Set<string>();
  for (const file of ROOTS.flatMap(walk)) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/t\(\s*"(society\.[a-z0-9_.]+)"\s*\)/g)) literal.add(m[1]);
    for (const m of text.matchAll(/t\(\s*`(society\.[a-z0-9_.]+)\.\$\{/g)) prefixes.add(m[1]);
  }
  return { literal, prefixes };
}

describe("society i18n parity", () => {
  const enKeys = blockKeys(en as Record<string, unknown>, enWorld as Record<string, unknown>);

  it("en carries a society block", () => {
    expect(enKeys.size).toBeGreaterThan(20);
  });

  it("every literal key the society surfaces use exists in en", () => {
    const { literal } = usedKeys();
    expect(literal.size).toBeGreaterThan(10);
    const missing = [...literal].filter((k) => !enKeys.has(k));
    expect(missing).toEqual([]);
  });

  it("every enum-driven prefix has at least one key in en", () => {
    const { prefixes } = usedKeys();
    for (const prefix of prefixes) {
      const hit = [...enKeys].some((k) => k.startsWith(`${prefix}.`));
      expect(hit, `no keys under ${prefix}`).toBe(true);
    }
  });

  it("de and es carry exactly the society keys en carries", () => {
    for (const [name, loc, world] of [
      ["de", de, deWorld],
      ["es", es, esWorld],
    ] as const) {
      const keys = blockKeys(loc as Record<string, unknown>, world as Record<string, unknown>);
      const missing = [...enKeys].filter((k) => !keys.has(k));
      const extra = [...keys].filter((k) => !enKeys.has(k));
      expect({ locale: name, missing, extra }).toEqual({ locale: name, missing: [], extra: [] });
    }
  });
});
