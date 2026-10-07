import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * No `:has()` rule may watch a whole shell region for a deep descendant.
 *
 * `main:has(> .stage #some-node)` looks harmless, but the engine has to
 * re-check it after EVERY DOM change anywhere inside <main> — which is the
 * whole app. A scrolling code editor rewrites its lines each frame, and that
 * one dead wiki rule turned every frame into a full-app style pass: about
 * 200 ms instead of 3 ms, so scrolling a diff ran at a few frames a second.
 *
 * Toggle a class or data attribute from the component instead. A `:has()`
 * limited to a small component (`.chip:has(> br:only-child)`) stays allowed.
 */

const SOURCE_ROOT = join(process.cwd(), "src");

/** A `:has()` on a shell-level element: html, body, main, or the section stage. */
const SHELL_HAS = /(?:^|[\s,{}>])(?:html|body|main|#root|\.jarvis-(?:section|visualization)-stage|\.jarvis-sheet)\s*:has\(/m;

describe("global :has() selectors", () => {
  it("never key a shell-level element on its descendants", () => {
    const css = readFileSync(join(SOURCE_ROOT, "index.css"), "utf8");
    const match = css.match(SHELL_HAS);
    expect(match?.[0] ?? null).toBeNull();
  });
});
