/**
 * Every `local_models.*` key a Local models panel asks for must exist in all
 * three locale chunks — and de/es must carry exactly the keys en carries.
 * The keys are read from the source files themselves, so a panel that adds a
 * string without its translations fails here, not in front of a user.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import en from "./locales/local_models/en.json";
import de from "./locales/local_models/de.json";
import es from "./locales/local_models/es.json";

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

const keysFor = (loc: Record<string, unknown>): Set<string> =>
  new Set(flatten(loc));

const SRC = join(__dirname, "..");
const PANEL_DIR = join(SRC, "views", "local-models");
const sources = (dir: string) =>
  readdirSync(dir)
    .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
    .map((f) => join(dir, f));
const SOURCE_FILES = [
  join(SRC, "views", "LocalModelsView.tsx"),
  ...sources(PANEL_DIR),
];

/** Literal keys only; template keys (`local_models.tune.${key}`) are covered
 *  by the parity check between the locales. */
function usedKeys(): Set<string> {
  const found = new Set<string>();
  for (const file of SOURCE_FILES) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/"(local_models\.[a-z0-9_.]+)"/g))
      found.add(m[1]);
  }
  return found;
}

describe("local models i18n parity", () => {
  it("en defines a non-trivial local_models key set", () => {
    expect(keysFor(en as Record<string, unknown>).size).toBeGreaterThan(200);
  });

  it("every key a panel asks for exists in en", () => {
    const enKeys = keysFor(en as Record<string, unknown>);
    const missing = [...usedKeys()].filter((k) => !enKeys.has(k));
    expect(missing).toEqual([]);
  });

  for (const [lang, loc] of [
    ["de", de],
    ["es", es],
  ] as const) {
    it(`${lang} has the same local_models keys as en`, () => {
      const enKeys = keysFor(en as Record<string, unknown>);
      const langKeys = keysFor(loc as Record<string, unknown>);
      const missing = [...enKeys].filter((k) => !langKeys.has(k));
      const extra = [...langKeys].filter((k) => !enKeys.has(k));
      expect({ missing, extra }).toEqual({ missing: [], extra: [] });
    });
  }
});
