import { renderHook, act } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { clampWidth, useResizablePane } from "./useResizablePane";

afterEach(() => window.localStorage.clear());

describe("clampWidth", () => {
  it("clamps below min and above max", () => {
    expect(clampWidth(50, 200, 480)).toBe(200);
    expect(clampWidth(900, 200, 480)).toBe(480);
    expect(clampWidth(300, 200, 480)).toBe(300);
  });

  it("rounds fractional widths to whole pixels", () => {
    expect(clampWidth(300.7, 200, 480)).toBe(301);
  });

  it("falls back to min on NaN", () => {
    expect(clampWidth(Number.NaN, 200, 480)).toBe(200);
  });
});

describe("useResizablePane", () => {
  const opts = { storageKey: "test.width", defaultWidth: 260, min: 200, max: 480 };

  it("starts at the default width when storage is empty", () => {
    const { result } = renderHook(() => useResizablePane(opts));
    expect(result.current.width).toBe(260);
  });

  it("restores a persisted width on mount", () => {
    window.localStorage.setItem("test.width", "320");
    const { result } = renderHook(() => useResizablePane(opts));
    expect(result.current.width).toBe(320);
  });

  it("clamps an out-of-band persisted width back into the band", () => {
    window.localStorage.setItem("test.width", "9999");
    const { result } = renderHook(() => useResizablePane(opts));
    expect(result.current.width).toBe(480);
  });

  it("reset() returns to the default and persists it", () => {
    window.localStorage.setItem("test.width", "300");
    const { result } = renderHook(() => useResizablePane(opts));
    act(() => result.current.reset());
    expect(result.current.width).toBe(260);
    expect(window.localStorage.getItem("test.width")).toBe("260");
  });
});
