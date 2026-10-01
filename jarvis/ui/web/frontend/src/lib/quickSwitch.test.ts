/**
 * The quick switcher must put the destination people mean FIRST, so Enter
 * without looking lands right — and it must not drift from the sidebar.
 */
import { describe, expect, it } from "vitest";
import { NAV_FOOTER_ITEMS, NAV_GROUPS, SETTINGS_HUB_ONLY_ITEMS } from "@/components/layout/navGroups";
import { SECTION_IDS } from "@/store/events";
import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";
import {
  QUICK_SWITCH_ENTRIES,
  ambiguousEntryKeys,
  normalizeQuery,
  rankQuickSwitch,
  scoreTerm,
  strongSettingsMatches,
} from "./quickSwitch";

/** Resolve labels from the shipped English dictionary, like the UI on "en". */
function englishLabel(item: { labelKey: string; fallbackLabel: string }): string {
  const value = item.labelKey
    .split(".")
    .reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], en);
  return typeof value === "string" ? value.replace("{name}", "Jarvis") : item.fallbackLabel;
}

const top = (query: string) => rankQuickSwitch(query, englishLabel)[0]?.entry.key;

describe("destinations", () => {
  it("covers every row the sidebar and the Settings hub show", () => {
    const sections = new Set(QUICK_SWITCH_ENTRIES.map((e) => e.section));
    for (const item of [...NAV_GROUPS.flat(), ...SETTINGS_HUB_ONLY_ITEMS, ...NAV_FOOTER_ITEMS]) {
      expect(sections.has(item.id)).toBe(true);
    }
  });

  it("only names real sections and has unique keys", () => {
    const keys = QUICK_SWITCH_ENTRIES.map((e) => e.key);
    expect(new Set(keys).size).toBe(keys.length);
    for (const e of QUICK_SWITCH_ENTRIES) expect(SECTION_IDS).toContain(e.section);
  });

  it("offers both faces of the front page", () => {
    const faces = QUICK_SWITCH_ENTRIES.filter((e) => e.section === "chats").map((e) => e.surface);
    expect(faces).toEqual(["voice", "chat"]);
  });
});

describe("ranking", () => {
  it("lists everything in sidebar order for an empty query", () => {
    const rows = rankQuickSwitch("", englishLabel);
    expect(rows.map((r) => r.entry.key)).toEqual(QUICK_SWITCH_ENTRIES.map((e) => e.key));
  });

  it.each([
    ["agentic", "agentic-ide"],
    ["Agentic IDE", "agentic-ide"],
    ["ide", "agentic-ide"],
    ["settings", "settings"],
    ["Einstellungen", "settings"], // i18n-allow: German query proves cross-locale search
    ["einst", "settings"], // i18n-allow: German query prefix
    ["ajustes", "settings"],
    ["skills", "skills"],
    ["api", "apikeys"],
    ["ollama", "local-models"],
    ["market", "marketplace"],
    ["wiki", "memory"],
  ])("puts the meant destination first for %j", (query, expected) => {
    expect(top(query)).toBe(expected);
  });

  it("finds an accented label from its plain spelling", () => {
    // i18n-allow: the German dictionary label carries an umlaut
    expect(rankQuickSwitch("worterbuch", englishLabel).map((r) => r.entry.key)).toContain("dictionary");
  });

  it("keeps a three-letter query to word starts", () => {
    const keys = rankQuickSwitch("ide", englishLabel).map((r) => r.entry.key);
    expect(keys).not.toContain("apikeys");
  });

  it("returns nothing for gibberish", () => {
    expect(rankQuickSwitch("zzqxv", englishLabel)).toEqual([]);
  });
});

describe("scoreTerm", () => {
  it("orders exact > prefix > word start > initials > substring", () => {
    const exact = scoreTerm("settings", "settings");
    const prefix = scoreTerm("settings", "sett");
    const word = scoreTerm("agentic ide", "ide");
    const initials = scoreTerm("run inspector", "ri");
    const inner = scoreTerm("marketplace", "place");
    expect(exact).toBeGreaterThan(prefix);
    expect(prefix).toBeGreaterThan(word);
    expect(word).toBeGreaterThan(initials);
    expect(initials).toBeGreaterThan(inner);
    expect(inner).toBeGreaterThan(0);
  });

  it("ignores one stray letter inside a word", () => {
    expect(scoreTerm("marketplace", "k")).toBe(0);
  });

  it("drops the {name} token from labels", () => {
    expect(normalizeQuery("{name} Voice")).toBe("voice");
  });
});

describe("strongSettingsMatches", () => {
  it("drops hits buried mid-word and caps the list", () => {
    const hits = [
      { label: "App", detail: "Which provider runs it" },
      { label: "Wake word", detail: "Say the name" },
      { label: "Wide layout" },
    ];
    expect(strongSettingsMatches("ide", hits)).toEqual([]);
    expect(strongSettingsMatches("wake", hits).map((h) => h.label)).toEqual(["Wake word"]);
    expect(strongSettingsMatches("w", [...hits, ...hits, ...hits], 2)).toHaveLength(2);
  });
});

/** Labels as a given UI language shows them. */
function labelIn(tree: Record<string, unknown>) {
  return (item: { labelKey: string; fallbackLabel: string }) => {
    const value = item.labelKey
      .split(".")
      .reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], tree);
    return typeof value === "string" ? value.replace("{name}", "Jarvis") : item.fallbackLabel;
  };
}

describe("typing a row's own name lands on that row", () => {
  // The bug this guards: "API Keys" is both a page and a voice tab, and the
  // switcher sent someone to the tab. A page must win its own name in every UI
  // language; a tab must at least be reachable by its name.
  for (const [language, tree] of [["en", en], ["de", de], ["es", es]] as const) {
    const labelFor = labelIn(tree);
    it(`in ${language}`, () => {
      for (const item of QUICK_SWITCH_ENTRIES) {
        const rows = rankQuickSwitch(labelFor(item), labelFor);
        if (item.parentLabelKey) {
          expect(rows.map((r) => r.entry.key), item.key).toContain(item.key);
        } else {
          expect(rows[0]?.entry.key, `${language}: "${labelFor(item)}"`).toBe(item.key);
        }
      }
    });
  }

  it("ranks the API Keys page above the voice tab of the same name", () => {
    const keys = rankQuickSwitch("API Keys", englishLabel).map((r) => r.entry.key);
    expect(keys.indexOf("apikeys")).toBeLessThan(keys.indexOf("voice-api-keys"));
    expect(ambiguousEntryKeys(englishLabel)).toEqual(new Set(["apikeys", "voice-api-keys"]));
  });
});
