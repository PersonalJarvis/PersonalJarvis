/**
 * Every lazily loaded section must actually resolve to a component.
 *
 * Why this test exists (2026-07-26): MainView switched from static imports to
 * `lazy(() => import(...).then((m) => ({ default: m.SomeView })))` to keep the
 * startup bundle small. That trade moves one class of error from build time to
 * click time — a wrong path or a mistyped export name no longer fails the
 * build, it fails silently until a user opens that one section and gets an
 * error boundary. TypeScript cannot catch it either, because the unwrap
 * produces `undefined` rather than a type error.
 *
 * So this walks MainView's own source, extracts each (module path, export
 * name) pair it lazily loads, and proves the export really exists. Reading the
 * source rather than a hand-kept list means a newly added section is covered
 * the moment it is written, and cannot be forgotten here.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const MAIN_VIEW = resolve(HERE, "MainView.tsx");

// Vite needs statically analysable globs, so every view module is collected up
// front and looked up by path below.
const viewModules = import.meta.glob("../../views/**/*.tsx");

/** `import("@/views/Foo").then((m) => ({ default: m.FooView }))` */
const LAZY_IMPORT_RE =
  /import\(\s*"@\/views\/([^"]+)"\s*\)\s*\.then\(\s*\([^)]*\)\s*=>\s*\(\{\s*default:\s*m\.(\w+)/g;

interface LazyView {
  modulePath: string;
  exportName: string;
}

function lazyViewsDeclaredInMainView(): LazyView[] {
  const source = readFileSync(MAIN_VIEW, "utf8");
  const found: LazyView[] = [];
  for (const match of source.matchAll(LAZY_IMPORT_RE)) {
    found.push({ modulePath: match[1], exportName: match[2] });
  }
  return found;
}

/** Map "@/views/x/Y" onto the key used by the glob above. */
function globKeyFor(modulePath: string): string {
  return `../../views/${modulePath}.tsx`;
}

describe("MainView lazy sections", () => {
  const lazyViews = lazyViewsDeclaredInMainView();

  it("declares lazily loaded sections at all", () => {
    // Guards the regex itself: if MainView's syntax drifts, every per-view
    // assertion below would vacuously pass on an empty list.
    expect(lazyViews.length).toBeGreaterThan(10);
  });

  it("keeps the default section statically imported", () => {
    // The first-paint section must not cost an extra round trip on startup.
    const source = readFileSync(MAIN_VIEW, "utf8");
    expect(source).toMatch(/import \{ ChatsView \} from "@\/views\/ChatsView"/);
    expect(lazyViews.some((v) => v.exportName === "ChatsView")).toBe(false);
  });

  it.each(lazyViewsDeclaredInMainView().map((v) => [v.modulePath, v.exportName]))(
    "@/views/%s really exports %s",
    async (modulePath, exportName) => {
      const loader = viewModules[globKeyFor(modulePath)];
      expect(
        loader,
        `MainView lazily imports "@/views/${modulePath}" but no such module exists`,
      ).toBeDefined();

      const mod = (await loader()) as Record<string, unknown>;
      expect(
        mod[exportName],
        `"@/views/${modulePath}" has no export named "${exportName}" - the ` +
          "section would resolve to undefined and blow up when opened",
      ).toBeTypeOf("function");
    },
  );
});
