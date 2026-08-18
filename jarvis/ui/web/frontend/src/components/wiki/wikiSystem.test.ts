import { describe, expect, it } from "vitest";

import { sunPalette } from "@/components/wiki/wikiSystem";

describe("sunPalette", () => {
  it("uses signal-yellow on dark and gold on light — never the same hex", () => {
    const dark = sunPalette("dark");
    const light = sunPalette("light");
    expect(dark.core).toBe(0xffd60a);
    expect(light.core).toBe(0xa86b00);
    expect(dark.core).not.toBe(light.core);
    expect(dark.ringOpacity).toBeLessThan(light.ringOpacity);
  });
});
