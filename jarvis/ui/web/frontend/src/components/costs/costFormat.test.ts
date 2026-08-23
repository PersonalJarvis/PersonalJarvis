import { describe, expect, it } from "vitest";
import {
  formatMoney,
  formatShare,
  formatTokens,
  keyColor,
  priceSourceTone,
  roleColor,
} from "./costFormat";

describe("formatMoney", () => {
  it("keeps sub-cent amounts visible instead of rounding them to zero", () => {
    // A single tool call really does cost $0.0004; "$0.00" would tell the user
    // their most frequent call is free.
    expect(formatMoney(0.0004, 0.92, "usd")).toBe("$0.0004");
  });

  it("uses two decimals for ordinary amounts and drops them for large ones", () => {
    expect(formatMoney(11.7, 0.92, "usd")).toBe("$11.70");
    // Locale-agnostic: the grouping separator is the test runner's locale,
    // the point of the assertion is that the cents are gone.
    expect(formatMoney(1234.56, 0.92, "usd")).toMatch(/^\$1[.,]235$/);
  });

  it("converts to EUR at the given rate", () => {
    expect(formatMoney(10, 0.9, "eur")).toBe("€9.00");
  });

  it("renders a true zero without decimals inflation", () => {
    expect(formatMoney(0, 0.92, "usd")).toBe("$0.00");
  });
});

describe("formatTokens", () => {
  it("scales the unit to the magnitude", () => {
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(1_240)).toBe("1.2k");
    expect(formatTokens(12_823_750)).toBe("12.82M");
  });
});

describe("formatShare", () => {
  it("never rounds a non-zero share down to 0%", () => {
    expect(formatShare(0)).toBe("0%");
    expect(formatShare(0.0002)).toBe("<0.1%");
    expect(formatShare(0.671)).toBe("67%");
  });
});

describe("colours", () => {
  it("gives every role its own fixed hue", () => {
    const roles = ["realtime", "tool", "pipeline", "agent", "worker"];
    expect(new Set(roles.map(roleColor)).size).toBe(roles.length);
  });

  it("keeps an arbitrary key's colour stable across renders", () => {
    expect(keyColor("gemini-live")).toBe(keyColor("gemini-live"));
    expect(keyColor("grok")).not.toBe(keyColor("gemini-live"));
  });

  it("routes a role key through the role palette", () => {
    expect(keyColor("tool")).toBe(roleColor("tool"));
  });
});

describe("priceSourceTone", () => {
  it("marks only an unpriced call as an error tone", () => {
    expect(priceSourceTone("recorded")).toBe("ok");
    expect(priceSourceTone("derived")).toBe("warn");
    expect(priceSourceTone("free")).toBe("off");
    expect(priceSourceTone("unknown")).toBe("error");
  });
});
