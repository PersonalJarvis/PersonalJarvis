import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The permission surfaces must work in BOTH themes and over a terminal pane,
 * so their colours come from theme tokens only (`bg-card`, `text-foreground`,
 * `border-border`, `text-muted-foreground`, ...), never from one fixed mode.
 *
 * jsdom cannot render a theme, so this is a static guard over the files that
 * draw the card, the Privacy page and the toast offset: no hex/rgb/hsl
 * literal, no raw palette utility (`bg-red-500`, `text-slate-900`), no
 * white/black, and no `dark:` or `light:` fork that would paint one mode by hand.
 * The manual T1 check (light, dark, over a terminal pane) still has to be done
 * on a build.
 */
const SOURCE_ROOT = join(process.cwd(), "src");
/**
 * Everything in `components/permissions/` is swept automatically, so a new
 * permission surface cannot slip past this guard by being left off the list.
 */
const SWEPT = readdirSync(join(SOURCE_ROOT, "components/permissions"))
  .filter((name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
  .map((name) => `components/permissions/${name}`);

const EXPLICIT = [
  "components/permissions/PermissionPromptLayer.tsx",
  "components/permissions/PermissionPromptHost.tsx",
  "components/permissions/promptActions.ts",
  "views/settings/PermissionsPanel.tsx",
  "components/ToastLayer.tsx",
  // The inline surfaces (the same explanation, in the place the person is looking at).
  "components/permissions/InlinePermissionNote.tsx",
  "components/permissions/ShortcutsStatusNote.tsx",
  "components/permissions/ShortcutsTip.tsx",
  "components/agentchat/DictationNote.tsx",
  "components/agentchat/DictationButton.tsx",
  "views/settings/MuteMusicPermissionNote.tsx",
  "hooks/useVoiceBlockedByPermission.ts",
];
const FILES = [...new Set([...EXPLICIT, ...SWEPT])];

const FORBIDDEN: Array<[string, RegExp]> = [
  ["hex colour", /#[0-9a-fA-F]{3,8}\b/],
  ["rgb()/hsl() literal", /\b(?:rgba?|hsla?)\s*\(/],
  [
    "raw palette utility",
    /\b(?:bg|text|border|ring|fill|stroke|from|to|via)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d{2,3}\b/,
  ],
  ["white/black utility", /\b(?:bg|text|border|ring|fill|stroke)-(?:white|black)\b/],
  ["dark:/light: fork", /(?:^|[\s"'`])(?:dark|light):/],
];

describe("permission surfaces use theme tokens only", () => {
  for (const file of FILES) {
    it(file, () => {
      const source = readFileSync(join(SOURCE_ROOT, file), "utf8")
        // Comments may name colours; the guard is about what is painted.
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:])\/\/.*$/gm, "$1");
      const offenders = FORBIDDEN.filter(([, pattern]) => pattern.test(source)).map(([name]) => name);

      expect(offenders, `${file} hardcodes a colour mode`).toEqual([]);
    });
  }
});

describe("the sweep", () => {
  it("finds the permission surfaces on disk", () => {
    expect(SWEPT).toEqual(
      expect.arrayContaining([
        "components/permissions/PermissionPromptLayer.tsx",
        "components/permissions/InlinePermissionNote.tsx",
      ]),
    );
  });
});

describe("the guard itself", () => {
  it("catches what it is meant to catch", () => {
    const bad = [
      'className="bg-[#1a1a1a]"',
      'style={{ color: "rgb(0, 0, 0)" }}',
      'className="text-slate-900"',
      'className="bg-white"',
      'className="dark:bg-black"',
    ];
    for (const sample of bad) {
      expect(FORBIDDEN.some(([, pattern]) => pattern.test(sample)), sample).toBe(true);
    }
    expect(FORBIDDEN.some(([, pattern]) => pattern.test('className="bg-card/95 text-foreground border-border"'))).toBe(false);
  });
});
