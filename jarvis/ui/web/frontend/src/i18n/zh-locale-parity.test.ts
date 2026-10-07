/**
 * The Simplified Chinese interface must be complete: every dictionary — the
 * main one and every lazy chunk — carries exactly the keys English carries,
 * and every translated string keeps the placeholders of its English source.
 * A key that exists only in English renders in English inside a Chinese UI; a
 * dropped `{name}` or `{0}` renders a sentence with a hole in it.
 */
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import zh from "./locales/zh.json";
import appshotEditorEn from "./locales/appshot_editor/en.json";
import appshotEditorZh from "./locales/appshot_editor/zh.json";
import computersEn from "./locales/computers/en.json";
import computersZh from "./locales/computers/zh.json";
import localModelsEn from "./locales/local_models/en.json";
import localModelsZh from "./locales/local_models/zh.json";
import marketplaceEn from "./locales/marketplace/en.json";
import marketplaceZh from "./locales/marketplace/zh.json";
import onboardingEn from "./locales/onboarding/en.json";
import onboardingZh from "./locales/onboarding/zh.json";
import paneReviewEn from "./locales/pane_review/en.json";
import paneReviewZh from "./locales/pane_review/zh.json";
import providersEn from "./locales/providers/en.json";
import providersZh from "./locales/providers/zh.json";
import societyEn from "./locales/society/en.json";
import societyZh from "./locales/society/zh.json";

type Tree = Record<string, unknown>;

const PAIRS: [string, Tree, Tree][] = [
  ["main", en, zh],
  ["appshot_editor", appshotEditorEn, appshotEditorZh],
  ["computers", computersEn, computersZh],
  ["local_models", localModelsEn, localModelsZh],
  ["marketplace", marketplaceEn, marketplaceZh],
  ["onboarding", onboardingEn, onboardingZh],
  ["pane_review", paneReviewEn, paneReviewZh],
  ["providers", providersEn, providersZh],
  ["society", societyEn, societyZh],
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

describe("Chinese interface dictionaries", () => {
  for (const [name, source, target] of PAIRS) {
    it(`${name}: zh carries exactly the keys en carries`, () => {
      expect([...leaves(target).keys()].sort()).toEqual([...leaves(source).keys()].sort());
    });

    it(`${name}: zh keeps every placeholder of the English string`, () => {
      const zhLeaves = leaves(target);
      const broken = [...leaves(source)]
        .filter(([key, text]) => {
          const got = placeholders(zhLeaves.get(key));
          const want = placeholders(text);
          return got.join("|") !== want.join("|");
        })
        .map(([key]) => key);
      expect(broken).toEqual([]);
    });
  }

  it("names itself in its own script", () => {
    expect(zh.languages_view.options.zh.label).toBe("中文");
  });
});
