import { describe, expect, it } from "vitest";

import { windowCollapsed } from "./AgenticTerminal";

describe("windowCollapsed", () => {
  it("treats a minimized desktop window's stub as unmeasurable", () => {
    // WebView2 shrinks the page to about 158x26 px while minimized.
    expect(windowCollapsed(158, 26)).toBe(true);
    expect(windowCollapsed(0, 0)).toBe(true);
  });

  it("measures any window a person could read a terminal in", () => {
    expect(windowCollapsed(1265, 762)).toBe(false);
    expect(windowCollapsed(2560, 1392)).toBe(false);
    expect(windowCollapsed(320, 160)).toBe(false);
  });
});
