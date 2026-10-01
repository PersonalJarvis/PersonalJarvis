import { describe, expect, it } from "vitest";
import cases from "../../../../../../../tests/fakes/terminal_repaint_cases.json";
import { FULL_SCREEN_ERASE_SCAN_TAIL, hasFullScreenErase } from "./terminalRepaint";

describe("full terminal repaint acknowledgement", () => {
  it.each(cases.full)("accepts a full redraw across any chunk boundary: %j", (erase) => {
    for (let split = 1; split < erase.length; split++) {
      expect(hasFullScreenErase(erase.slice(0, split))).toBe(false);
      const tail = erase.slice(0, split).slice(-FULL_SCREEN_ERASE_SCAN_TAIL);
      expect(hasFullScreenErase(tail + erase.slice(split))).toBe(true);
    }
  });

  it.each(cases.partial)("does not release the curtain for a partial erase: %j", (erase) => {
    expect(hasFullScreenErase(erase)).toBe(false);
  });
});
