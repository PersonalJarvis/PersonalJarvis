/**
 * Every `agent_chat.*` / `all_chats.*` key the front page's chat asks for
 * must exist in en — and de/es must carry exactly the keys en carries in
 * those two blocks. The keys are read from the source files themselves, so
 * a composer or timeline string added without its translations fails here,
 * not in front of a person. Modelled on local-models-i18n-parity.test.ts.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";

const BLOCKS = ["agent_chat", "all_chats"] as const;
type Block = (typeof BLOCKS)[number];

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

/** The keys of one block, prefixed with the block name (`agent_chat.send`). */
function blockKeys(loc: Record<string, unknown>, block: Block): Set<string> {
  const sub = loc[block];
  if (!sub || typeof sub !== "object") return new Set();
  return new Set(flatten(sub as Record<string, unknown>, block));
}

const SRC = join(__dirname, "..");
const AGENTCHAT_DIR = join(SRC, "components", "agentchat");
const SOURCE_FILES = [
  ...readdirSync(AGENTCHAT_DIR)
    .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
    .map((f) => join(AGENTCHAT_DIR, f)),
  join(SRC, "components", "home", "ChatStage.tsx"),
  join(SRC, "components", "home", "RecentChats.tsx"),
  join(SRC, "components", "home", "AllChatsDialog.tsx"),
  join(SRC, "components", "home", "chatRows.ts"),
];

/** Literal keys only; a template key would be covered by the parity check. */
function usedKeys(): Set<string> {
  const found = new Set<string>();
  for (const file of SOURCE_FILES) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/"((?:agent_chat|all_chats)\.[a-z0-9_.]+)"/g)) found.add(m[1]);
  }
  return found;
}

describe("agent chat i18n parity", () => {
  it("reads a non-trivial set of keys from the chat's source files", () => {
    expect(usedKeys().size).toBeGreaterThan(40);
  });

  it("every key the chat asks for exists in en", () => {
    const enKeys = new Set([...blockKeys(en, "agent_chat"), ...blockKeys(en, "all_chats")]);
    const missing = [...usedKeys()].filter((k) => !enKeys.has(k)).sort();
    expect(missing).toEqual([]);
  });

  for (const [lang, loc] of [
    ["de", de],
    ["es", es],
  ] as const) {
    for (const block of BLOCKS) {
      it(`${lang} has the same ${block} keys as en`, () => {
        const enKeys = blockKeys(en, block);
        const langKeys = blockKeys(loc, block);
        const missing = [...enKeys].filter((k) => !langKeys.has(k)).sort();
        const extra = [...langKeys].filter((k) => !enKeys.has(k)).sort();
        expect({ missing, extra }).toEqual({ missing: [], extra: [] });
      });
    }
  }
});
