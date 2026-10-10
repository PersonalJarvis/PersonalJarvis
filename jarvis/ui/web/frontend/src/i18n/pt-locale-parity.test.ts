/**
 * The European Portuguese interface must be complete: every dictionary — the
 * main one and every lazy chunk — carries exactly the keys English carries,
 * and every translated string keeps the placeholders of its English source.
 * A key that exists only in English renders in English inside a Portuguese UI;
 * a dropped `{name}` or `{0}` renders a sentence with a hole in it.
 */
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import pt from "./locales/pt.json";
import appshotEditorEn from "./locales/appshot_editor/en.json";
import appshotEditorPt from "./locales/appshot_editor/pt.json";
import computersEn from "./locales/computers/en.json";
import computersPt from "./locales/computers/pt.json";
import localModelsEn from "./locales/local_models/en.json";
import localModelsPt from "./locales/local_models/pt.json";
import marketplaceEn from "./locales/marketplace/en.json";
import marketplacePt from "./locales/marketplace/pt.json";
import onboardingEn from "./locales/onboarding/en.json";
import onboardingPt from "./locales/onboarding/pt.json";
import paneReviewEn from "./locales/pane_review/en.json";
import paneReviewPt from "./locales/pane_review/pt.json";
import providersEn from "./locales/providers/en.json";
import providersPt from "./locales/providers/pt.json";
import societyEn from "./locales/society/en.json";
import societyPt from "./locales/society/pt.json";

type Tree = Record<string, unknown>;

const PAIRS: [string, Tree, Tree][] = [
  ["main", en, pt],
  ["appshot_editor", appshotEditorEn, appshotEditorPt],
  ["computers", computersEn, computersPt],
  ["local_models", localModelsEn, localModelsPt],
  ["marketplace", marketplaceEn, marketplacePt],
  ["onboarding", onboardingEn, onboardingPt],
  ["pane_review", paneReviewEn, paneReviewPt],
  ["providers", providersEn, providersPt],
  ["society", societyEn, societyPt],
];

function leaves(tree: Tree, prefix = ""): Map<string, unknown> {
  const out = new Map<string, unknown>();
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [k, v] of leaves(value as Tree, path)) out.set(k, v);
    } else {
      out.set(path, value);
    }
  }
  return out;
}

const PLACEHOLDER = /\{\w+\}/g;
const placeholders = (text: unknown) =>
  typeof text === "string" ? (text.match(PLACEHOLDER) ?? []).sort() : [];

describe("European Portuguese interface dictionaries", () => {
  for (const [name, source, target] of PAIRS) {
    it(`${name}: pt carries exactly the keys en carries`, () => {
      expect([...leaves(target).keys()].sort()).toEqual([...leaves(source).keys()].sort());
    });

    it(`${name}: pt keeps every placeholder of the English string`, () => {
      const ptLeaves = leaves(target);
      const broken = [...leaves(source)]
        .filter(([key, text]) => {
          const got = placeholders(ptLeaves.get(key));
          const want = placeholders(text);
          return got.join("|") !== want.join("|");
        })
        .map(([key]) => key);
      expect(broken).toEqual([]);
    });
  }

  it("names itself in its own language", () => {
    expect(pt.languages_view.options.pt.label).toBe("Português (Portugal)");
  });

  it("sends its own code with slash commands", () => {
    // ChatCommands.tsx forwards this value as the command locale; anything
    // else would make Portuguese slash commands answer in English.
    expect(pt.slash.locale).toBe("pt");
  });

  it("formats plurals and numbers as European Portuguese", () => {
    // Bare "pt" is Brazilian in CLDR (it counts 0 as singular); pt-PT does not.
    expect(pt.trace_report.locale).toBe("pt-PT");
  });
});
