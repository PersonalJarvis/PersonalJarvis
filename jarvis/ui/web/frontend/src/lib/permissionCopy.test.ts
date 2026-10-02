import { afterEach, beforeEach, describe, expect, it } from "vitest";
import de from "@/i18n/locales/de.json";
import en from "@/i18n/locales/en.json";
import es from "@/i18n/locales/es.json";
import { loadUiLocale, translate, useI18nStore } from "@/i18n";
import { PERMISSION_FEATURES, PERMISSION_NEEDED_REASONS } from "./permissionEvents";
import { listPermissionNames, promptCopyKey, promptSentence } from "./permissionCopy";

type Tree = Record<string, unknown>;
const LOCALES: Record<string, Tree> = { en, de, es };

function at(tree: Tree, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>((node, part) => (node && typeof node === "object" ? (node as Tree)[part] : undefined), tree);
}

describe("every pair the card can render has a full sentence in all three locales", () => {
  for (const [locale, tree] of Object.entries(LOCALES)) {
    it(`${locale}: ${PERMISSION_FEATURES.length} features x ${PERMISSION_NEEDED_REASONS.length} reasons, plus the generic fallback`, () => {
      const missing: string[] = [];
      for (const feature of [...PERMISSION_FEATURES, "generic"]) {
        for (const reason of PERMISSION_NEEDED_REASONS) {
          const key = `permissions.prompt.${feature}.${reason}`;
          const value = at(tree, key);
          if (typeof value !== "string" || value.trim() === "") {
            missing.push(key);
            continue;
          }
          // A full sentence: it ends like one and names the permissions it is about.
          if (!/[.!?]$/.test(value)) missing.push(`${key} (no sentence end)`);
          if (!value.includes("{permissions}")) missing.push(`${key} (no {permissions})`);
        }
      }
      expect(missing).toEqual([]);
    });

    it(`${locale}: a heading per reason and every card action`, () => {
      for (const reason of PERMISSION_NEEDED_REASONS) {
        expect(at(tree, `permissions.prompt.heading.${reason}`), reason).toEqual(expect.any(String));
      }
      for (const action of ["continue", "open_settings", "check_again", "not_now", "restart", "allow_outside", "reset"]) {
        expect(at(tree, `permissions.prompt.action.${action}`), action).toEqual(expect.any(String));
      }
    });

    it(`${locale}: the pieces of the card exist`, () => {
      for (const key of [
        "still_off",
        "reset_asked",
        "reset_manual",
        "outside_note",
        "name",
        "more",
        "details",
        "allowed",
        "steps_label",
      ]) {
        expect(at(tree, `permissions.prompt.${key}`), key).toEqual(expect.any(String));
      }
    });

    it(`${locale}: the retired wizard and banner copy is gone`, () => {
      const permissions = tree.permissions as Tree;
      for (const retired of [
        "banner",
        "features",
        "optional",
        "optional_hint",
        "restart_deferred",
        "setup_all",
        "setup_wait_prompt",
        "setup_cancel",
        "identity_reset",
      ]) {
        expect(permissions, retired).not.toHaveProperty(retired);
      }
    });
  }

  it("only ever substitutes the two tokens the builder fills", () => {
    const allowed = new Set(["app", "permissions", "name"]);
    const offenders: string[] = [];
    for (const [locale, tree] of Object.entries(LOCALES)) {
      const prompt = at(tree, "permissions.prompt") as Tree;
      const visit = (node: unknown, path: string) => {
        if (typeof node === "string") {
          for (const token of node.match(/\{(\w+)\}/g) ?? []) {
            const name = token.slice(1, -1);
            // `name` / `more` / `action.allow_outside` carry their own documented tokens.
            if (!allowed.has(name) && !["0", "n"].includes(name)) offenders.push(`${locale}:${path} ${token}`);
          }
        } else if (node && typeof node === "object") {
          for (const [key, value] of Object.entries(node)) visit(value, `${path}.${key}`);
        }
      };
      visit(prompt, "permissions.prompt");
    }
    expect(offenders).toEqual([]);
  });
});

describe("promptCopyKey", () => {
  it("maps a known feature to its own sentence and an unknown one to the generic copy", () => {
    expect(promptCopyKey("dictation", "denied")).toBe("permissions.prompt.dictation.denied");
    expect(promptCopyKey("teleport", "denied")).toBe("permissions.prompt.generic.denied");
  });
});

describe("rendering a sentence", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
  });
  afterEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
  });

  it("fills the app name and the quoted permission names", () => {
    const sentence = promptSentence({
      t: translate,
      language: "en",
      episode: { feature: "dictation", reason: "needs_settings", permissions: ["microphone"] },
      appName: "Personal Jarvis",
    });

    expect(sentence).toBe(
      "Dictation needs access to “Microphone”. Switch it on for Personal Jarvis in System Settings, then come back.",
    );
  });

  it("joins two permissions as a list in the UI language", () => {
    expect(listPermissionNames(translate, ["screen_recording", "accessibility"], "en")).toBe(
      "“Screen Recording” and “Accessibility”",
    );
  });

  it("never leaves a token or a bare key in the sentence, in any locale", async () => {
    for (const language of ["en", "de", "es"] as const) {
      // German and Spanish load on demand: wait for the dictionary, or this would read English.
      await loadUiLocale(language);
      useI18nStore.getState().setUi(language, { push: false });
      for (const feature of [...PERMISSION_FEATURES, "teleport"]) {
        for (const reason of PERMISSION_NEEDED_REASONS) {
          const sentence = promptSentence({
            t: translate,
            language,
            episode: { feature, reason, permissions: ["microphone", "accessibility"] },
            appName: "Personal Jarvis",
          });
          expect(sentence, `${language} ${feature} ${reason}`).not.toMatch(/\{\w+\}|permissions\.prompt/);
        }
      }
    }
  });

  it("falls back to the product name when the backend sent none", () => {
    const sentence = promptSentence({
      t: translate,
      language: "en",
      episode: { feature: "voice", reason: "restart_hint", permissions: ["microphone"] },
      appName: "",
    });

    expect(sentence).toContain("Personal Jarvis");
  });
});
